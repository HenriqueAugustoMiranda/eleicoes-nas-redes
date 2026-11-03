import sqlite3
from pathlib import Path
from datetime import datetime
import sys

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "whatsapp.sqlite"
OUTPUTS = ROOT / "tests" / "outputs"
EXPORTS = ROOT / "exports"

if not Path("/media/hen/ssd").is_mount():
    sys.exit("ERRO: SSD não está montado.")

con = sqlite3.connect(DB, timeout=60)
con.execute("PRAGMA busy_timeout=60000")
con.execute("PRAGMA foreign_keys=ON")

def tabela_existe(nome):
    return con.execute("""
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name=?
    """, (nome,)).fetchone() is not None

try:
    print("=== VALIDANDO A TABELA MENSAGENS ===")

    colunas = {
        row[1] for row in
        con.execute("PRAGMA table_info(mensagens)")
    }

    if not {"mensagem_id", "canal_id"}.issubset(colunas):
        raise RuntimeError("Estrutura de mensagens inesperada.")

    duplicados = con.execute("""
        SELECT mensagem_id, COUNT(*)
        FROM mensagens
        WHERE mensagem_id IS NOT NULL
        GROUP BY mensagem_id
        HAVING COUNT(*) > 1
        LIMIT 10
    """).fetchall()

    if duplicados:
        print("IDs duplicados encontrados:")
        for item in duplicados:
            print(item)
        raise RuntimeError(
            "Não é seguro criar a chave estrangeira. Migração cancelada."
        )

    print("IDs únicos: OK")

    encaminhamentos_antes = None

    if "encaminhamentos" in colunas:
        encaminhamentos_antes = con.execute("""
            SELECT COUNT(encaminhamentos),
                   COALESCE(SUM(encaminhamentos), 0)
            FROM mensagens
        """).fetchone()

    # Backup consistente
    backup = ROOT / "db" / (
        "whatsapp_backup_antes_reacoes_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".sqlite"
    )

    destino = sqlite3.connect(backup)
    con.backup(destino)
    destino.close()

    print("\nBackup:", backup)

    print("\n=== LIMPEZA DA COLETA ANTERIOR ===")

    con.execute("BEGIN IMMEDIATE")

    # Remover tabelas antigas de reações.
    con.execute("DROP TABLE IF EXISTS reacoes")
    con.execute("DROP TABLE IF EXISTS reacoes_newsletter")

    # Remover eventual progresso antigo de reações.
    con.execute("DROP TABLE IF EXISTS estado_coleta_reacoes")

    # Limpar o agregado antigo, preservando encaminhamentos.
    if tabela_existe("engajamento_newsletter"):
        eng_cols = {
            row[1]
            for row in con.execute(
                "PRAGMA table_info(engajamento_newsletter)"
            )
        }

        if "total_reacoes" in eng_cols:
            con.execute("""
                UPDATE engajamento_newsletter
                SET total_reacoes = 0
            """)

    # Garantir unicidade da chave referenciada.
    indice_unico = False

    for item in con.execute("PRAGMA index_list(mensagens)"):
        nome = item[1]
        unico = item[2]
        parcial = item[4]

        if not unico or parcial:
            continue

        indice_cols = [
            row[2]
            for row in con.execute(
                f'PRAGMA index_info("{nome}")'
            )
        ]

        if indice_cols == ["mensagem_id"]:
            indice_unico = True
            break

    if not indice_unico:
        con.execute("""
            CREATE UNIQUE INDEX idx_mensagens_mensagem_id_reacoes
            ON mensagens(mensagem_id)
        """)

    # Tabela definitiva: SOMENTE mensagem_id, emoji e quantidade.
    con.execute("""
        CREATE TABLE reacoes (
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
        CREATE INDEX idx_reacoes_emoji
        ON reacoes(emoji)
    """)

    # Progresso da coleta fica separado da tabela de reações.
    con.execute("""
        CREATE TABLE estado_coleta_reacoes (
            canal_id TEXT PRIMARY KEY,
            cursor_before INTEGER NOT NULL DEFAULT 0,
            concluida INTEGER NOT NULL DEFAULT 0,
            atualizado_em TEXT NOT NULL
        )
    """)

    con.commit()

    # Garantir que os encaminhamentos permaneceram intactos.
    if encaminhamentos_antes is not None:
        depois = con.execute("""
            SELECT COUNT(encaminhamentos),
                   COALESCE(SUM(encaminhamentos), 0)
            FROM mensagens
        """).fetchone()

        if tuple(encaminhamentos_antes) != tuple(depois):
            raise RuntimeError(
                "Os encaminhamentos mudaram inesperadamente."
            )

    erros_fk = con.execute("PRAGMA foreign_key_check").fetchall()

    if erros_fk:
        raise RuntimeError(f"Erro de chave estrangeira: {erros_fk[:10]}")

    print("\n=== ESTRUTURA NOVA ===")

    for row in con.execute("PRAGMA table_info(reacoes)"):
        print(row)

    print("\nChave estrangeira:")
    for row in con.execute("PRAGMA foreign_key_list(reacoes)"):
        print(row)

    print("\nIntegridade:", con.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0])

finally:
    con.close()

# Apagar arquivos de exportação da coleta antiga.
arquivos = []

for pasta, padroes in [
    (OUTPUTS, [
        "reacoes_*.jsonl",
        "diagnostico_reacoes*.json",
        "reacoes_sem_correspondencia.csv",
        "coleta_engajamento.log",
        "coleta_anteriores.log",
    ]),
    (EXPORTS, [
        "reacoes_por_publicacao.csv",
    ])
]:
    for padrao in padroes:
        arquivos.extend(pasta.glob(padrao))

for arquivo in set(arquivos):
    if arquivo.is_file():
        arquivo.unlink()
        print("Arquivo antigo removido:", arquivo.name)

print("\nLIMPEZA CONCLUÍDA.")
print("A coluna mensagens.encaminhamentos foi preservada.")
