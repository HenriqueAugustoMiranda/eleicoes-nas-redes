import sqlite3
from pathlib import Path
from datetime import datetime
import sys

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "whatsapp.sqlite"

if not DB.exists():
    sys.exit(f"Banco não encontrado: {DB}")

if not Path("/media/hen/ssd").is_mount():
    sys.exit("ERRO: SSD não está montado.")

con = sqlite3.connect(DB, timeout=60)
con.execute("PRAGMA busy_timeout = 60000")
con.execute("PRAGMA foreign_keys = ON")

def tabela_existe(nome):
    return con.execute("""
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = ?
    """, (nome,)).fetchone() is not None

def indices_unicos_mensagem():
    for item in con.execute("PRAGMA index_list(mensagens)"):
        nome = item[1]
        unico = item[2]
        parcial = item[4]

        if not unico or parcial:
            continue

        colunas = [
            row[2]
            for row in con.execute(
                f'PRAGMA index_info("{nome}")'
            )
        ]

        if colunas == ["mensagem_id"]:
            return True

    return False

try:
    print("=" * 60)
    print("MIGRAÇÃO DAS REAÇÕES PARA A TABELA MENSAGENS")
    print("=" * 60)

    for tabela in (
        "mensagens",
        "engajamento_newsletter",
        "reacoes_newsletter"
    ):
        if not tabela_existe(tabela):
            sys.exit(f"ERRO: tabela obrigatória ausente: {tabela}")

    # Verificar duplicidade do identificador das mensagens.
    duplicados = con.execute("""
        SELECT mensagem_id, COUNT(*)
        FROM mensagens
        WHERE mensagem_id IS NOT NULL
        GROUP BY mensagem_id
        HAVING COUNT(*) > 1
        LIMIT 10
    """).fetchall()

    if duplicados:
        print("ERRO: existem IDs de mensagem duplicados:")
        for item in duplicados:
            print(item)
        sys.exit("Migração cancelada sem modificar o banco.")

    # Validar duplicidades na origem da migração.
    duplicados_origem = con.execute("""
        SELECT e.mensagem_id, r.emoji, COUNT(*)
        FROM reacoes_newsletter r
        JOIN engajamento_newsletter e
          ON e.canal_id = r.canal_id
         AND e.server_id = r.server_id
        JOIN mensagens m
          ON m.canal_id = e.canal_id
         AND m.mensagem_id = e.mensagem_id
        GROUP BY e.mensagem_id, r.emoji
        HAVING COUNT(*) > 1
        LIMIT 10
    """).fetchall()

    if duplicados_origem:
        print("ERRO: pares mensagem/emoji duplicados na origem:")
        for item in duplicados_origem:
            print(item)
        sys.exit("Migração cancelada sem modificar o banco.")

    quantidade_origem = con.execute("""
        SELECT COUNT(*)
        FROM reacoes_newsletter r
        JOIN engajamento_newsletter e
          ON e.canal_id = r.canal_id
         AND e.server_id = r.server_id
        JOIN mensagens m
          ON m.canal_id = e.canal_id
         AND m.mensagem_id = e.mensagem_id
    """).fetchone()[0]

    mensagens_origem = con.execute("""
        SELECT COUNT(DISTINCT e.mensagem_id)
        FROM reacoes_newsletter r
        JOIN engajamento_newsletter e
          ON e.canal_id = r.canal_id
         AND e.server_id = r.server_id
        JOIN mensagens m
          ON m.canal_id = e.canal_id
         AND m.mensagem_id = e.mensagem_id
    """).fetchone()[0]

    sem_vinculo = con.execute("""
        SELECT COUNT(*)
        FROM reacoes_newsletter r
        JOIN engajamento_newsletter e
          ON e.canal_id = r.canal_id
         AND e.server_id = r.server_id
        LEFT JOIN mensagens m
          ON m.canal_id = e.canal_id
         AND m.mensagem_id = e.mensagem_id
        WHERE m.mensagem_id IS NULL
    """).fetchone()[0]

    print("Pares mensagem/emoji disponíveis:", quantidade_origem)
    print("Mensagens com reações:", mensagens_origem)
    print("Reações sem mensagem correspondente:", sem_vinculo)

    if quantidade_origem == 0:
        sys.exit("Nenhuma reação correspondente encontrada.")

    # Caso já exista a tabela, conferir se sua estrutura é compatível.
    if tabela_existe("reacoes"):
        colunas = con.execute(
            "PRAGMA table_info(reacoes)"
        ).fetchall()

        esperadas = {
            "mensagem_id": 1,
            "emoji": 2,
            "quantidade": 0
        }

        atuais = {
            row[1]: row[5]
            for row in colunas
        }

        if any(
            atuais.get(nome) != pk
            for nome, pk in esperadas.items()
        ):
            sys.exit(
                "Tabela reacoes já existe com estrutura incompatível. "
                "Nenhuma alteração realizada."
            )

    # Backup consistente antes da alteração.
    backup = ROOT / "db" / (
        "whatsapp_antes_tabela_reacoes_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".sqlite"
    )

    destino = sqlite3.connect(backup)
    con.backup(destino)
    destino.close()

    print("\nBackup criado:", backup)

    con.execute("BEGIN IMMEDIATE")

    # SQLite exige uma chave única na coluna referenciada
    # pela chave estrangeira.
    if not indices_unicos_mensagem():
        con.execute("""
            CREATE UNIQUE INDEX idx_mensagens_id_unico
            ON mensagens(mensagem_id)
        """)
        print("Índice único de mensagem_id criado.")

    con.execute("""
        CREATE TABLE IF NOT EXISTS reacoes (
            mensagem_id TEXT NOT NULL,
            emoji TEXT NOT NULL,
            quantidade INTEGER NOT NULL
                CHECK (quantidade > 0),

            PRIMARY KEY (mensagem_id, emoji),

            FOREIGN KEY (mensagem_id)
                REFERENCES mensagens(mensagem_id)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        )
    """)

    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_reacoes_emoji
        ON reacoes(emoji)
    """)

    # Remover eventuais reações antigas que já não existam
    # na tabela de origem.
    con.execute("""
        DELETE FROM reacoes
        WHERE NOT EXISTS (
            SELECT 1
            FROM reacoes_newsletter r
            JOIN engajamento_newsletter e
              ON e.canal_id = r.canal_id
             AND e.server_id = r.server_id
            JOIN mensagens m
              ON m.canal_id = e.canal_id
             AND m.mensagem_id = e.mensagem_id
            WHERE e.mensagem_id = reacoes.mensagem_id
              AND r.emoji = reacoes.emoji
        )
    """)

    # Importação das reações, preservando os emojis exatamente
    # como vieram do WhatsApp.
    con.execute("""
        INSERT INTO reacoes (
            mensagem_id,
            emoji,
            quantidade
        )
        SELECT
            e.mensagem_id,
            r.emoji,
            r.quantidade
        FROM reacoes_newsletter r
        JOIN engajamento_newsletter e
          ON e.canal_id = r.canal_id
         AND e.server_id = r.server_id
        JOIN mensagens m
          ON m.canal_id = e.canal_id
         AND m.mensagem_id = e.mensagem_id
        WHERE r.quantidade > 0

        ON CONFLICT(mensagem_id, emoji)
        DO UPDATE SET
            quantidade = excluded.quantidade
    """)

    # Validar as relações antes de confirmar a transação.
    erros_fk = con.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if erros_fk:
        raise RuntimeError(
            f"Falha na validação das chaves estrangeiras: {erros_fk[:10]}"
        )

    con.commit()

    print("\n" + "=" * 60)
    print("MIGRAÇÃO CONCLUÍDA")
    print("=" * 60)

    total = con.execute("""
        SELECT COUNT(*) FROM reacoes
    """).fetchone()[0]

    mensagens = con.execute("""
        SELECT COUNT(DISTINCT mensagem_id)
        FROM reacoes
    """).fetchone()[0]

    soma = con.execute("""
        SELECT SUM(quantidade) FROM reacoes
    """).fetchone()[0] or 0

    emojis = con.execute("""
        SELECT COUNT(DISTINCT emoji)
        FROM reacoes
    """).fetchone()[0]

    print("Registros de emoji/mensagem:", total)
    print("Mensagens com reações:", mensagens)
    print("Emojis diferentes:", emojis)
    print("Soma das reações:", soma)
    print("Reações não migradas por falta de vínculo:", sem_vinculo)

    print("\nIntegridade:", con.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0])

finally:
    con.close()
