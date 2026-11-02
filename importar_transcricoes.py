import csv
import sqlite3
import argparse
from pathlib import Path

BASE = Path("/media/hen/ssd/projetos/teste-canal-whatsapp")
DB = BASE / "db/whatsapp.sqlite"

parser = argparse.ArgumentParser()
parser.add_argument("--aplicar", action="store_true")
parser.add_argument("--csv", type=Path)
args = parser.parse_args()

if args.csv:
    CSV_FILE = args.csv.expanduser().resolve()
else:
    candidatos = [
        BASE / "exports/transcricoes_whisper.csv",
        BASE / "transcricoes_whisper.csv",
        Path.home() / "projetos/teste-canal-whatsapp/transcricoes_whisper.csv",
        Path.home() / "projetos/teste-canal-whatsapp/exports/transcricoes_whisper.csv"
    ]

    encontrados = [p for p in candidatos if p.is_file()]

    if len(encontrados) != 1:
        print("Arquivos encontrados:", encontrados)
        raise SystemExit(
            "Não foi possível determinar o CSV. "
            "Use --csv /caminho/transcricoes_whisper.csv"
        )

    CSV_FILE = encontrados[0]

if not DB.is_file():
    raise SystemExit(f"Banco não encontrado: {DB}")

if not CSV_FILE.is_file():
    raise SystemExit(f"CSV não encontrado: {CSV_FILE}")

print("Banco:", DB)
print("CSV:", CSV_FILE)

# 1. Ler transcrições
transcricoes = {}
conflitos = set()
duplicadas = 0
vazias = 0
total_csv = 0

with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
    leitor = csv.DictReader(f, delimiter=";")

    colunas = leitor.fieldnames or []

    if not {"mensagem_id", "transcricao"}.issubset(colunas):
        raise SystemExit(
            f"CSV precisa conter mensagem_id e transcricao. "
            f"Encontradas: {colunas}"
        )

    for linha in leitor:
        total_csv += 1

        mid = (linha.get("mensagem_id") or "").strip()
        texto = linha.get("transcricao") or ""

        if not mid:
            vazias += 1
            continue

        if not texto.strip():
            vazias += 1
            continue

        if mid in transcricoes:
            if transcricoes[mid] == texto:
                duplicadas += 1
            else:
                conflitos.add(mid)
        else:
            transcricoes[mid] = texto

# Não importar IDs com transcrições conflitantes
for mid in conflitos:
    transcricoes.pop(mid, None)

# 2. Abrir banco
db = sqlite3.connect(DB)

colunas_db = {
    row[1] for row in db.execute("PRAGMA table_info(mensagens)")
}

tem_original = "texto_original_whatsapp" in colunas_db
tem_transcricao = "transcricao_whisper" in colunas_db

# 3. Conferir correspondências
atualizacoes = []
nao_encontradas = []
ambiguas = []
ja_atualizadas = 0

for mid, transcricao in transcricoes.items():

    registros = db.execute("""
        SELECT canal_id, mensagem_id, texto
        FROM mensagens
        WHERE mensagem_id = ?
    """, (mid,)).fetchall()

    if len(registros) == 0:
        nao_encontradas.append(mid)
        continue

    if len(registros) > 1:
        ambiguas.append(mid)
        continue

    canal_id, mensagem_id, texto_atual = registros[0]

    original = None
    transcricao_anterior = None

    if tem_original:
        original = db.execute("""
            SELECT texto_original_whatsapp
            FROM mensagens
            WHERE canal_id = ? AND mensagem_id = ?
        """, (canal_id, mensagem_id)).fetchone()[0]

    if tem_transcricao:
        transcricao_anterior = db.execute("""
            SELECT transcricao_whisper
            FROM mensagens
            WHERE canal_id = ? AND mensagem_id = ?
        """, (canal_id, mensagem_id)).fetchone()[0]

    if original is None:
        original = texto_atual or ""

    # Concatenação determinística: não duplica em novas execuções
    if original.strip():
        texto_final = (
            original
            + "\n\n[TRANSCRIÇÃO WHISPER]\n"
            + transcricao
        )
    else:
        texto_final = transcricao

    if (
        tem_original
        and tem_transcricao
        and transcricao_anterior == transcricao
        and texto_atual == texto_final
    ):
        ja_atualizadas += 1
        continue

    atualizacoes.append((
        canal_id,
        mensagem_id,
        original,
        transcricao,
        texto_final
    ))

# 4. Relatório
print("\n========== RESULTADO DA ANÁLISE ==========")
print("Registros no CSV:", total_csv)
print("IDs únicos válidos:", len(transcricoes))
print("Duplicatas idênticas:", duplicadas)
print("IDs com conflitos:", len(conflitos))
print("Transcrições vazias:", vazias)
print("Correspondências encontradas:",
      len(atualizacoes) + ja_atualizadas)
print("Transcrições que serão importadas:", len(atualizacoes))
print("Já importadas corretamente:", ja_atualizadas)
print("IDs não encontrados:", len(nao_encontradas))
print("IDs ambíguos:", len(ambiguas))

if nao_encontradas:
    print("\nExemplos não encontrados:")
    for mid in nao_encontradas[:10]:
        print("-", mid)

if ambiguas:
    print("\nExemplos ambíguos:")
    for mid in ambiguas[:10]:
        print("-", mid)

if not args.aplicar:
    print("\nSIMULAÇÃO: nenhuma alteração foi realizada.")
    print("Para importar, execute com --aplicar.")

else:
    try:
        with db:
            # Criar colunas de preservação, caso ainda não existam
            if not tem_original:
                db.execute("""
                    ALTER TABLE mensagens
                    ADD COLUMN texto_original_whatsapp TEXT
                """)

            if not tem_transcricao:
                db.execute("""
                    ALTER TABLE mensagens
                    ADD COLUMN transcricao_whisper TEXT
                """)

            db.executemany("""
                UPDATE mensagens
                SET
                    texto_original_whatsapp = ?,
                    transcricao_whisper = ?,
                    texto = ?
                WHERE canal_id = ? AND mensagem_id = ?
            """, [
                (original, transcricao, texto_final, canal_id, mensagem_id)
                for canal_id, mensagem_id, original,
                    transcricao, texto_final in atualizacoes
            ])

        print("\nIMPORTAÇÃO CONCLUÍDA!")
        print("Mensagens atualizadas:", len(atualizacoes))

    except Exception:
        db.rollback()
        raise

db.close()
