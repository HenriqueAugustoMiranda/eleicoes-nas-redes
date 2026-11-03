import sqlite3
import pandas as pd
import re
import json
from pathlib import Path
from datetime import datetime

BASE = Path("/media/hen/ssd/projetos/teste-canal-whatsapp")
DB = BASE / "db/whatsapp.sqlite"

# Padrão para identificar URLs
padrao_url = re.compile(
    r'https?://[^\s<>"\']+|www\.[^\s<>"\']+',
    flags=re.IGNORECASE
)

# Conectar ao banco
con = sqlite3.connect(DB)

# Carregar mensagens
df = pd.read_sql_query("""
    SELECT *
    FROM mensagens
    ORDER BY timestamp
""", con)

# Extrair links
def extrair_links(texto):
    if not isinstance(texto, str):
        return []

    links = padrao_url.findall(texto)

    # Remover pontuação final
    links = [link.rstrip('.,;:!?)]}') for link in links]

    # Remover duplicatas, preservando a ordem
    return list(dict.fromkeys(links))

df['links'] = df['texto'].apply(extrair_links)

# Criar backup do banco
backup = DB.with_name(
    f"whatsapp-antes-links-{datetime.now():%Y%m%d-%H%M%S}.sqlite"
)

backup_con = sqlite3.connect(backup)
con.backup(backup_con)
backup_con.close()

# Verificar se a coluna já existe
colunas = {
    linha[1]
    for linha in con.execute("PRAGMA table_info(mensagens)")
}

if 'links' not in colunas:
    con.execute("ALTER TABLE mensagens ADD COLUMN links TEXT")

# Preparar atualização
dados = [
    (
        json.dumps(links, ensure_ascii=False),
        canal_id,
        mensagem_id
    )
    for links, canal_id, mensagem_id
    in zip(df['links'], df['canal_id'], df['mensagem_id'])
]

# Atualizar somente a coluna links
with con:
    con.executemany("""
        UPDATE mensagens
        SET links = ?
        WHERE canal_id = ? AND mensagem_id = ?
    """, dados)

# Resumo
print("Importação concluída!")
print("Mensagens analisadas:", len(df))
print("Mensagens com links:", (df['links'].str.len() > 0).sum())
print("Links identificados:", df['links'].str.len().sum())
print("Backup:", backup)

con.close()