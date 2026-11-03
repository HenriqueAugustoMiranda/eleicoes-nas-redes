package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	_ "github.com/mattn/go-sqlite3"
	"os"
	"path/filepath"
	"sort"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	waLog "go.mau.fi/whatsmeow/util/log"
)

type Channel struct {
	ID   string `json:"id"`
	Name string `json:"nome"`
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "ERRO:", err)
		os.Exit(1)
	}
}

func run() error {
	ctx := context.Background()

	root := filepath.Clean(filepath.Join(".", "..", ".."))

	data, err := os.ReadFile(
		filepath.Join(root, "config", "canais.json"),
	)
	if err != nil {
		return err
	}

	var channels []Channel
	if err := json.Unmarshal(data, &channels); err != nil {
		return err
	}

	authPath, err := filepath.Abs("auth.db")
	if err != nil {
		return err
	}

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
	if err != nil {
		return err
	}
	if device == nil || device.ID == nil {
		return errors.New("sessão autenticada não encontrada")
	}

	client := whatsmeow.NewClient(
		device,
		waLog.Stdout("UpdatesProbe", "WARN", true),
	)

	if err := client.Connect(); err != nil {
		return err
	}
	defer client.Disconnect()

	fmt.Println("Conectando à sessão existente...")

	connected := false
	for i := 0; i < 120; i++ {
		if client.IsLoggedIn() {
			connected = true
			break
		}
		time.Sleep(500 * time.Millisecond)
	}

	if !connected {
		return errors.New("WhatsApp não autenticou")
	}

	fmt.Println("WhatsApp conectado!")
	fmt.Println("Consulta de atualizações sem gravação no SQLite.")

	since := time.Date(
		2026, time.September, 1,
		0, 0, 0, 0, time.UTC,
	)

	for _, channel := range channels {
		jid, err := types.ParseJID(channel.ID)
		if err != nil {
			return err
		}

		fmt.Println("\n======================================")
		fmt.Println("CANAL:", channel.Name)
		fmt.Println("======================================")

		queryCtx, cancel := context.WithTimeout(
			ctx,
			90*time.Second,
		)

		updates, err := client.GetNewsletterMessageUpdates(
			queryCtx,
			jid,
			&whatsmeow.GetNewsletterUpdatesParams{
				Count: 100,
				Since: since,
			},
		)

		cancel()

		if err != nil {
			fmt.Println("Erro na consulta:", err)
			continue
		}

		fmt.Println("Atualizações retornadas:", len(updates))

		var (
			viewsPositive     int
			reactionsPositive int
			totalViews        int64
			totalReactions    int64
			minID             int64
			maxID             int64
			minDate           time.Time
			maxDate           time.Time
		)

		for _, msg := range updates {
			id := int64(msg.MessageServerID)

			if minID == 0 || id < minID {
				minID = id
				minDate = msg.Timestamp
			}
			if id > maxID {
				maxID = id
				maxDate = msg.Timestamp
			}

			totalViews += int64(msg.ViewsCount)

			if msg.ViewsCount > 0 {
				viewsPositive++
			}

			quantity := 0
			for _, count := range msg.ReactionCounts {
				quantity += count
			}

			totalReactions += int64(quantity)

			if quantity > 0 {
				reactionsPositive++
			}
		}

		fmt.Println("Menor server_id:", minID)
		fmt.Println("Maior server_id:", maxID)
		fmt.Println("Data mais antiga:", minDate.Format(time.RFC3339))
		fmt.Println("Data mais recente:", maxDate.Format(time.RFC3339))
		fmt.Println("Publicações com visualizações positivas:", viewsPositive)
		fmt.Println("Soma das visualizações retornadas:", totalViews)
		fmt.Println("Publicações com reações:", reactionsPositive)
		fmt.Println("Soma das reações retornadas:", totalReactions)

		fmt.Println("\nAmostra das atualizações:")

		limit := len(updates)
		if limit > 5 {
			limit = 5
		}

		for _, msg := range updates[:limit] {
			emojis := make([]string, 0, len(msg.ReactionCounts))
			for emoji := range msg.ReactionCounts {
				emojis = append(emojis, emoji)
			}
			sort.Strings(emojis)

			fmt.Printf(
				"\nID=%d | Mensagem=%s | Data=%s\n",
				msg.MessageServerID,
				msg.MessageID,
				msg.Timestamp.Format(time.RFC3339),
			)

			fmt.Printf(
				"Visualizações=%d | Encaminhamentos=%d\n",
				msg.ViewsCount,
				msg.ForwardsCount,
			)

			for _, emoji := range emojis {
				fmt.Printf(
					"  %s: %d\n",
					emoji,
					msg.ReactionCounts[emoji],
				)
			}
		}
	}

	return nil
}
