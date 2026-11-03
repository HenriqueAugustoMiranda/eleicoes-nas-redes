package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"

	_ "github.com/mattn/go-sqlite3"
	qrterminal "github.com/mdp/qrterminal/v3"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
)

type Channel struct {
	ID   string `json:"id"`
	Name string `json:"nome"`
}

type queryer interface {
	QueryRow(string, ...any) *sql.Row
}

type executor interface {
	queryer
	Exec(string, ...any) (sql.Result, error)
}

type Stats struct {
	Messages       int
	Linked         int
	Unmatched      int
	ReactionRows   int
	TotalReactions int64
	Samples        []string
}

func findMessageID(
	q queryer,
	channel Channel,
	rawID types.MessageID,
) (string, error) {
	if rawID == "" {
		return "", nil
	}

	// O whatsmeow fornece o ID bruto.
	// A tabela mensagens guarda o ID serializado do whatsapp-web.js.
	//
	// O canal é usado SOMENTE para reconstruir e validar
	// o ID completo da mensagem. Não será armazenado em reacoes.

	serializedID := fmt.Sprintf(
		"false_%s_%s",
		channel.ID,
		string(rawID),
	)

	var storedID string

	err := q.QueryRow(`
		SELECT mensagem_id
		FROM mensagens
		WHERE canal_id = ?
		  AND mensagem_id = ?
		LIMIT 1
	`, channel.ID, serializedID).Scan(&storedID)

	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}

	return storedID, err
}

func schema(db *sql.DB) error {
	duplicate := 0

	err := db.QueryRow(`
		SELECT COUNT(*)
		FROM (
			SELECT mensagem_id
			FROM mensagens
			WHERE mensagem_id IS NOT NULL
			GROUP BY mensagem_id
			HAVING COUNT(*) > 1
		)
	`).Scan(&duplicate)

	if err != nil {
		return err
	}

	if duplicate > 0 {
		return errors.New(
			"mensagem_id possui duplicidades; chave estrangeira não pode ser criada",
		)
	}

	_, err = db.Exec(`
		CREATE UNIQUE INDEX IF NOT EXISTS
			idx_mensagens_mensagem_id_reacoes
		ON mensagens(mensagem_id)
	`)
	if err != nil {
		return err
	}

	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS reacoes (
			mensagem_id TEXT NOT NULL,
			emoji TEXT NOT NULL,
			quantidade INTEGER NOT NULL CHECK(quantidade > 0),
			PRIMARY KEY(mensagem_id, emoji),
			FOREIGN KEY(mensagem_id)
				REFERENCES mensagens(mensagem_id)
				ON UPDATE CASCADE
				ON DELETE CASCADE
		)
	`)
	if err != nil {
		return err
	}

	_, err = db.Exec(`
		CREATE INDEX IF NOT EXISTS idx_reacoes_emoji
		ON reacoes(emoji)
	`)
	if err != nil {
		return err
	}

	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS estado_coleta_reacoes (
			canal_id TEXT PRIMARY KEY,
			cursor_before INTEGER NOT NULL DEFAULT 0,
			concluida INTEGER NOT NULL DEFAULT 0,
			atualizado_em TEXT NOT NULL
		)
	`)

	return err
}

func connectWhatsApp(client *whatsmeow.Client) error {
	if client.Store.ID != nil {
		fmt.Println("Reutilizando sessão existente...")

		if err := client.Connect(); err != nil {
			return err
		}

		return waitLoggedIn(client)
	}

	qrChan, err := client.GetQRChannel(context.Background())
	if err != nil {
		return err
	}

	if err := client.Connect(); err != nil {
		return err
	}

	for evt := range qrChan {
		switch evt.Event {
		case "code":
			fmt.Println("\nEscaneie o QR Code:")
			qrterminal.GenerateHalfBlock(
				evt.Code,
				qrterminal.L,
				os.Stdout,
			)

		case "success":
			fmt.Println("Pareamento concluído!")
			return waitLoggedIn(client)

		case "timeout":
			return errors.New("QR Code expirou")
		}
	}

	return errors.New("canal QR encerrado sem pareamento")
}

func waitLoggedIn(client *whatsmeow.Client) error {
	for i := 0; i < 120; i++ {
		if client.IsLoggedIn() {
			fmt.Println("WhatsApp autenticado e conectado!")
			return nil
		}
		time.Sleep(500 * time.Millisecond)
	}

	return errors.New("tempo esgotado aguardando conexão")
}

func processPage(
	db *sql.DB,
	channel Channel,
	messages []*types.NewsletterMessage,
	write bool,
	cursor int64,
) (Stats, error) {
	stats := Stats{
		Messages: len(messages),
	}

	var tx *sql.Tx
	var q executor = db

	if write {
		var err error

		tx, err = db.Begin()
		if err != nil {
			return stats, err
		}
		defer tx.Rollback()

		q = tx
	}

	seen := make(map[string]bool)

	for _, msg := range messages {
		storedID, err := findMessageID(
			q,
			channel,
			msg.MessageID,
		)

		if err != nil {
			return stats, err
		}

		if storedID == "" {
			stats.Unmatched++
			continue
		}

		if seen[storedID] {
			continue
		}
		seen[storedID] = true

		stats.Linked++

		if len(stats.Samples) < 5 {
			stats.Samples = append(
				stats.Samples,
				fmt.Sprintf(
					"%s -> %s",
					msg.MessageID,
					storedID,
				),
			)
		}

		emojis := make([]string, 0, len(msg.ReactionCounts))

		for emoji := range msg.ReactionCounts {
			emojis = append(emojis, emoji)
		}

		sort.Strings(emojis)

		if write {
			// Atualiza a fotografia de reações da mensagem.
			_, err := tx.Exec(`
				DELETE FROM reacoes
				WHERE mensagem_id = ?
			`, storedID)

			if err != nil {
				return stats, err
			}
		}

		for _, emoji := range emojis {
			quantity := msg.ReactionCounts[emoji]

			if quantity <= 0 {
				continue
			}

			stats.ReactionRows++
			stats.TotalReactions += int64(quantity)

			if write {
				_, err := tx.Exec(`
					INSERT INTO reacoes (
						mensagem_id,
						emoji,
						quantidade
					)
					VALUES (?, ?, ?)
				`,
					storedID,
					emoji,
					quantity,
				)

				if err != nil {
					return stats, err
				}
			}
		}
	}

	if write {
		_, err := tx.Exec(`
			INSERT INTO estado_coleta_reacoes (
				canal_id,
				cursor_before,
				concluida,
				atualizado_em
			)
			VALUES (?, ?, 0, ?)

			ON CONFLICT(canal_id) DO UPDATE SET
				cursor_before = excluded.cursor_before,
				concluida = 0,
				atualizado_em = excluded.atualizado_em
		`,
			channel.ID,
			cursor,
			time.Now().UTC().Format(time.RFC3339Nano),
		)

		if err != nil {
			return stats, err
		}

		if err := tx.Commit(); err != nil {
			return stats, err
		}
	}

	return stats, nil
}

func markComplete(
	db *sql.DB,
	channelID string,
	cursor int64,
) error {
	_, err := db.Exec(`
		INSERT INTO estado_coleta_reacoes (
			canal_id,
			cursor_before,
			concluida,
			atualizado_em
		)
		VALUES (?, ?, 1, ?)

		ON CONFLICT(canal_id) DO UPDATE SET
			cursor_before = excluded.cursor_before,
			concluida = 1,
			atualizado_em = excluded.atualizado_em
	`,
		channelID,
		cursor,
		time.Now().UTC().Format(time.RFC3339Nano),
	)

	return err
}

func getState(
	db *sql.DB,
	channelID string,
) (int64, bool, error) {
	var cursor int64
	var completed int

	err := db.QueryRow(`
		SELECT cursor_before, concluida
		FROM estado_coleta_reacoes
		WHERE canal_id = ?
	`, channelID).Scan(&cursor, &completed)

	if errors.Is(err, sql.ErrNoRows) {
		return 0, false, nil
	}

	return cursor, completed == 1, err
}

func fetchPage(
	client *whatsmeow.Client,
	channel Channel,
	before int64,
	count int,
) ([]*types.NewsletterMessage, error) {
	jid, err := types.ParseJID(channel.ID)
	if err != nil {
		return nil, err
	}

	ctx, cancel := context.WithTimeout(
		context.Background(),
		90*time.Second,
	)
	defer cancel()

	return client.GetNewsletterMessages(
		ctx,
		jid,
		&whatsmeow.GetNewsletterMessagesParams{
			Count:  count,
			Before: types.MessageServerID(before),
		},
	)
}

func runChannel(
	client *whatsmeow.Client,
	db *sql.DB,
	channel Channel,
	all bool,
	limit int,
) error {
	fmt.Println("\n======================================")
	fmt.Println("CANAL:", channel.Name)
	fmt.Println("======================================")

	before := int64(0)

	if all {
		cursor, completed, err := getState(db, channel.ID)
		if err != nil {
			return err
		}

		if completed {
			fmt.Println("Canal já concluído. Pulando.")
			return nil
		}

		before = cursor
	}

	totalMessages := 0
	totalLinked := 0
	totalUnmatched := 0
	totalRows := 0
	totalReactions := int64(0)

	pageSize := 100

	if !all {
		pageSize = limit
	}

	for page := 1; ; page++ {
		messages, err := fetchPage(
			client,
			channel,
			before,
			pageSize,
		)

		if err != nil {
			return fmt.Errorf("página %d: %w", page, err)
		}

		if len(messages) == 0 {
			if all {
				if err := markComplete(db, channel.ID, before); err != nil {
					return err
				}
			}

			fmt.Println("Fim do histórico disponível.")
			break
		}

		var oldest int64

		for _, msg := range messages {
			id := int64(msg.MessageServerID)

			if id <= 0 {
				return fmt.Errorf(
					"server_id inválido na página %d",
					page,
				)
			}

			if oldest == 0 || id < oldest {
				oldest = id
			}
		}

		if all && before > 0 && oldest >= before {
			return fmt.Errorf(
				"paginação não avançou: cursor=%d, menor ID=%d",
				before,
				oldest,
			)
		}

		stats, err := processPage(
			db,
			channel,
			messages,
			all,
			oldest,
		)

		if err != nil {
			return fmt.Errorf("erro na página %d: %w", page, err)
		}

		totalMessages += stats.Messages
		totalLinked += stats.Linked
		totalUnmatched += stats.Unmatched
		totalRows += stats.ReactionRows
		totalReactions += stats.TotalReactions

		mode := "PRÉVIA"
		if all {
			mode = "GRAVADO"
		}

		fmt.Printf(
			"Página %d | %s | Publicações: %d | Vinculadas: %d | Sem vínculo: %d | Registros emoji: %d | Reações: %d\n",
			page,
			mode,
			stats.Messages,
			stats.Linked,
			stats.Unmatched,
			stats.ReactionRows,
			stats.TotalReactions,
		)

		if page == 1 && !all && len(stats.Samples) > 0 {
			fmt.Println("\nExemplos de mapeamento:")
			for _, sample := range stats.Samples {
				fmt.Println(sample)
			}
		}

		if !all {
			fmt.Println("\nPrévia finalizada. Nenhum dado foi gravado.")
			break
		}

		before = oldest

		// Páginas parciais não significam fim do histórico.
		time.Sleep(300 * time.Millisecond)
	}

	fmt.Printf(
		"\nResumo %s | Lidas: %d | Vinculadas: %d | Sem vínculo: %d | Emojis: %d | Reações: %d\n",
		channel.Name,
		totalMessages,
		totalLinked,
		totalUnmatched,
		totalRows,
		totalReactions,
	)

	return nil
}

func run() error {
	all := flag.Bool(
		"all",
		false,
		"Coletar e gravar todo o histórico disponível",
	)

	limit := flag.Int(
		"limit",
		100,
		"Quantidade consultada por canal no modo prévia",
	)

	flag.Parse()

	if *limit <= 0 {
		return errors.New("limit precisa ser maior que zero")
	}

	root, err := filepath.Abs("../..")
	if err != nil {
		return err
	}

	configPath := filepath.Join(root, "config", "canais.json")
	mainDBPath := filepath.Join(root, "db", "whatsapp.sqlite")

	data, err := os.ReadFile(configPath)
	if err != nil {
		return err
	}

	var channels []Channel
	if err := json.Unmarshal(data, &channels); err != nil {
		return err
	}

	mainURI := "file:" + filepath.ToSlash(mainDBPath) +
		"?_foreign_keys=on&_busy_timeout=30000"

	db, err := sql.Open("sqlite3", mainURI)
	if err != nil {
		return err
	}
	defer db.Close()

	db.SetMaxOpenConns(1)

	if err := db.Ping(); err != nil {
		return err
	}

	if _, err := db.Exec("PRAGMA foreign_keys=ON"); err != nil {
		return err
	}

	if err := schema(db); err != nil {
		return fmt.Errorf("erro no esquema das reações: %w", err)
	}

	fmt.Println("Banco:", mainDBPath)
	fmt.Println("Tabela de destino: reacoes")
	fmt.Println("Chave estrangeira: mensagens.mensagem_id")
	fmt.Println("Modo de coleta completa:", *all)

	ctx := context.Background()

	authPath := filepath.Join(root, "tests", "whatsmeow-reacoes", "auth-reacoes-v2.db")

	container, err := sqlstore.New(
		ctx,
		"sqlite3",
		"file:"+filepath.ToSlash(authPath)+"?_foreign_keys=on",
		nil,
	)
	if err != nil {
		return err
	}

	defer container.Close()

	device, err := container.GetFirstDevice(ctx)

	if errors.Is(err, sql.ErrNoRows) || device == nil {
		device = container.NewDevice()
	} else if err != nil {
		return err
	}

	client := whatsmeow.NewClient(
		device,
		waLog.Stdout("Reacoes", "WARN", true),
	)

	defer client.Disconnect()

	if err := connectWhatsApp(client); err != nil {
		return err
	}

	for _, channel := range channels {
		if err := runChannel(
			client,
			db,
			channel,
			*all,
			*limit,
		); err != nil {
			return fmt.Errorf("canal %s: %w", channel.Name, err)
		}
	}

	if *all {
		var rows, messages, emojis int64
		var total int64

		err := db.QueryRow(`
			SELECT
				COUNT(*),
				COUNT(DISTINCT mensagem_id),
				COUNT(DISTINCT emoji),
				COALESCE(SUM(quantidade), 0)
			FROM reacoes
		`).Scan(&rows, &messages, &emojis, &total)

		if err != nil {
			return err
		}

		fmt.Println("\n======================================")
		fmt.Println("RESUMO FINAL DAS REAÇÕES")
		fmt.Println("======================================")
		fmt.Println("Registros mensagem/emoji:", rows)
		fmt.Println("Mensagens com reações:", messages)
		fmt.Println("Emojis diferentes:", emojis)
		fmt.Println("Soma das reações:", total)

		fkRows, err := db.Query("PRAGMA foreign_key_check")
		if err != nil {
			return err
		}
		defer fkRows.Close()

		if fkRows.Next() {
			return errors.New("foram encontradas violações de chave estrangeira")
		}

		fmt.Println("Chaves estrangeiras: OK")
	}

	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "\nERRO:", err)
		os.Exit(1)
	}
}
