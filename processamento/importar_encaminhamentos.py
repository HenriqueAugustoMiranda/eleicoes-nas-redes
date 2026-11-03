import sqlite3
import json
import sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "whatsapp.sqlite"
OUTPUTS = ROOT / "tests" / "outputs"

arquivos = sorted(
    OUTPUTS.glob("encaminhamentos_wwebjs_*.jsonl"),
    key=lambda p: p.stat().st_mtime_ns
)

if not arquivos:
    sys.exit("ERRO: nenhum arquivo JSONL de encaminhamentos encontrado.")

ARQUIVO = arquivos[-1]

print("=" * 60)
print("IMPORTAÇÃO DE ENCAMINHAMENTOS")
print("=" * 60)
print("Arquivo:", ARQUIVO.name)

# Validar todos os registros antes de alterar o banco.
registros = {}
invalidos = 0

with ARQUIVO.open(encoding="utf-8") as f:
    for numero, linha in enumerate(f, 1):
        if not linha.strip():
            continue

        try:
            r = json.loads(linha)

            canal = r["canal_id"]
            mensagem_id = r["mensagem_id"]
            quantidade = r.get("encaminhamentos")

            if not isinstance(canal, str) or not canal:
                raise ValueError("canal_id inválido")

            if not isinstance(mensagem_id, str) or not mensagem_id:
                raise ValueError("mensagem_id inválido")

            if quantidade is None:
                invalidos += 1
                continue

            if isinstance(quantidade, bool) or not isinstance(quantidade, int):
                raise ValueError("encaminhamentos não é inteiro")

            if quantidade < 0:
                raise ValueError("encaminhamentos negativo")

            registros[(canal, mensagem_id)] = quantidade

        except Exception as e:
            raise SystemExit(
                f"ERRO na linha {numero}: {e}"
            )

if not registros:
    sys.exit("Nenhum registro válido para importar.")

print("Registros únicos válidos:", len(registros))
print("Registros sem contagem válida:", invalidos)

con = sqlite3.connect(DB, timeout=30)
con.execute("PRAGMA busy_timeout = 30000")

try:
    con.execute("BEGIN IMMEDIATE")

    colunas = {
        row[1]
        for row in con.execute("PRAGMA table_info(mensagens)")
    }

    if "encaminhamentos" not in colunas:
        con.execute("""
            ALTER TABLE mensagens
            ADD COLUMN encaminhamentos INTEGER
        """)
        print("Coluna encaminhamentos criada.")
    else:
        print("Coluna encaminhamentos já existe.")

    atualizar = con.cursor()

    encontrados = Counter()
    ausentes = []

    for (canal, mensagem_id), quantidade in registros.items():
        atualizar.execute("""
            UPDATE mensagens
            SET encaminhamentos = ?
            WHERE canal_id = ?
              AND mensagem_id = ?
        """, (quantidade, canal, mensagem_id))

        if atualizar.rowcount > 0:
            encontrados[canal] += atualizar.rowcount
        else:
            ausentes.append((canal, mensagem_id))

    con.commit()

    print("\n" + "=" * 60)
    print("RESULTADO DA IMPORTAÇÃO")
    print("=" * 60)

    for canal, quantidade in encontrados.items():
        print(f"{canal}: {quantidade} mensagens atualizadas")

    print("Total atualizado:", sum(encontrados.values()))
    print("Sem correspondência:", len(ausentes))

    if ausentes:
        arquivo_ausentes = OUTPUTS / "encaminhamentos_sem_correspondencia.csv"

        import csv

        with arquivo_ausentes.open(
            "w",
            newline="",
            encoding="utf-8"
        ) as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["canal_id", "mensagem_id"])
            writer.writerows(ausentes)

        print("IDs não encontrados:", arquivo_ausentes)

except Exception:
    con.rollback()
    raise

finally:
    con.close()
