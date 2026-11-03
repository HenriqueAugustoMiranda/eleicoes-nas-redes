# eleicoes-nas-redes

Este projeto coleta publicações de canais do WhatsApp, salva as mensagens em um banco de dados e baixa as mídias disponíveis. Também permite exportar as mensagens para CSV e importar transcrições feitas com o Whisper.

A coleta é feita pelo WhatsApp Web, usando `whatsapp-web.js`. É necessário ter uma sessão ativa do WhatsApp.

## 1. O que é necessário

- Linux;
- Node.js 24 e npm;
- Python 3;
- SQLite 3;
- acesso ao WhatsApp no celular;
- SSD montado em `/media/hen/ssd`.

O projeto foi organizado para ser executado neste caminho:

```text
/media/hen/ssd/projetos/teste-canal-whatsapp
```

## 2. Preparar o projeto

Abra o terminal e entre na pasta:

```bash
mountpoint -q /media/hen/ssd || {
    echo "SSD não está montado."
    exit 1
}

cd /media/hen/ssd/projetos/teste-canal-whatsapp
```

Na primeira utilização, instale as dependências:

```bash
npm ci
```

Confira se o SQLite está instalado:

```bash
sqlite3 --version
```

Caso o comando não exista, instale-o com:

```bash
sudo apt install sqlite3
```

## 3. Configurar os canais

A lista dos canais fica em `config/canais.json`. Esse arquivo é local e não é enviado ao GitHub.

Na primeira configuração, crie ou edite o arquivo:

```bash
nano config/canais.json
```

Use este formato, substituindo os exemplos pelos dados dos canais que serão coletados:

```json
[
    {
        "id": "ID_DO_CANAL@newsletter",
        "nome": "Nome do canal"
    }
]
```

Para localizar um canal pelo nome, execute:

```bash
npm run buscar:canal -- "Nome do canal"
```

Copie o identificador exibido para o arquivo `config/canais.json`. É possível cadastrar mais de um canal na lista.

## 4. Conectar ao WhatsApp

Na primeira execução, o WhatsApp Web poderá exibir um QR Code no terminal. Abra o WhatsApp no celular, entre em **Aparelhos conectados** e leia o código.

A sessão fica salva na pasta `.wwebjs_auth`. Nas próximas execuções, normalmente não será necessário ler o QR Code novamente.

Não apague essa pasta se quiser manter a sessão conectada.

## 5. Executar a coleta principal

Com os canais configurados e o WhatsApp conectado, execute:

```bash
npm run coletar:whatsapp
```

Esse comando consulta o histórico disponibilizado pelo WhatsApp para cada canal, registra as publicações no banco e tenta baixar as mídias suportadas, como imagens, áudios e vídeos convencionais.

As figurinhas não são baixadas. Os vídeos redondos também são tratados separadamente.

A coleta pode ser executada novamente. As mensagens já registradas e os arquivos baixados e validados são preservados; novas publicações disponíveis podem ser adicionadas.

## 6. Coletar os vídeos redondos

Depois da coleta principal, execute:

```bash
npm run coletar:ptv
```

Esse comando procura os vídeos redondos dos canais e os salva separadamente dos vídeos convencionais. Os arquivos são conferidos antes de serem registrados como baixados.

## 7. Exportar as mensagens para CSV

Para gerar ou atualizar a exportação das mensagens, execute:

```bash
npm run exportar
```

O arquivo será salvo em:

```text
exports/mensagens.csv
```

A exportação contém o canal, os identificadores da mensagem, data, tipo, texto e indicação de mídia.

## 8. Importar transcrições do Whisper

O arquivo de transcrições deve ficar em:

```text
exports/transcricoes_whisper.csv
```

Ele deve usar ponto e vírgula (`;`) como separador e conter estas duas colunas:

```csv
mensagem_id;transcricao
```

Cada transcrição precisa ter o identificador da publicação correspondente em `mensagem_id`.

Primeiro, execute a simulação:

```bash
npm run importar:transcricoes
```

A simulação mostra quantas transcrições serão associadas, quantos identificadores não foram encontrados e se existem duplicatas ou conflitos. Ela não altera o banco.

Se o resultado estiver correto, faça a importação:

```bash
npm run importar:transcricoes -- --aplicar
```

A importação preserva o texto original da publicação e acrescenta a transcrição. Ela também guarda os conteúdos separadamente no banco para evitar duplicações em uma nova execução.

## 9. Onde ficam os dados

| Caminho | Conteúdo |
|---|---|
| `db/whatsapp.sqlite` | Banco de dados com canais, mensagens e informações das mídias |
| `midias/` | Imagens, áudios e vídeos baixados |
| `exports/` | Arquivos CSV exportados e transcrições do Whisper |
| `.wwebjs_auth/` | Sessão do WhatsApp Web |
| `config/canais.json` | Canais configurados para coleta |

Faça cópias de segurança do banco e dos arquivos de mídia. O `.gitignore` impede que esses dados locais sejam enviados ao Git, mas não substitui um backup.

## 10. Comandos principais

Execute os comandos a partir da pasta raiz do projeto:

| Ação | Comando |
|---|---|
| Buscar um canal | `npm run buscar:canal -- "Nome do canal"` |
| Coletar mensagens e mídias comuns | `npm run coletar:whatsapp` |
| Coletar vídeos redondos | `npm run coletar:ptv` |
| Exportar mensagens para CSV | `npm run exportar` |
| Simular importação do Whisper | `npm run importar:transcricoes` |
| Aplicar importação do Whisper | `npm run importar:transcricoes -- --aplicar` |

## Observações

- O histórico retornado depende do conteúdo que o WhatsApp disponibiliza ao WhatsApp Web. A coleta não garante, por si só, que todas as publicações desde a criação do canal estejam disponíveis.
- Publicações apagadas ou revogadas podem não ter mais o conteúdo original disponível.
- Figurinhas ficam fora do escopo da coleta.
- Os vídeos redondos são coletados pelo comando separado `npm run coletar:ptv`.
- Antes de executar qualquer coleta, confira se o SSD está montado e se o terminal está na pasta raiz do projeto.