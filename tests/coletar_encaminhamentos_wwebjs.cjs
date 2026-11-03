const { Client, LocalAuth } = require('whatsapp-web.js');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');

const CONFIG = path.join(ROOT, 'config', 'canais.json');
const AUTH_DIR = path.join(ROOT, '.wwebjs_auth');
const OUTPUT_DIR = path.join(ROOT, 'tests', 'outputs');

const LIMITE = 10000;

const canais = JSON.parse(
    fs.readFileSync(CONFIG, 'utf8')
);

fs.mkdirSync(OUTPUT_DIR, { recursive: true });

const agora = new Date().toISOString()
    .replace(/[:.]/g, '-');

const OUTPUT = path.join(
    OUTPUT_DIR,
    `encaminhamentos_wwebjs_${agora}.jsonl`
);

const client = new Client({
    authStrategy: new LocalAuth({
        clientId: 'canal-teste',
        dataPath: AUTH_DIR
    }),
    puppeteer: {
        headless: true
    }
});

let finalizado = false;

console.log('======================================');
console.log('COLETOR DE ENCAMINHAMENTOS');
console.log('Fonte: whatsapp-web.js');
console.log('Campo: rawData.forwardsCount');
console.log('======================================');

client.on('qr', qr => {
    console.log('QR Code necessário. Escaneie pelo WhatsApp.');

    require('qrcode-terminal').generate(qr, {
        small: true
    });
});

client.on('authenticated', () => {
    console.log('Sessão autenticada.');
});

client.on('auth_failure', erro => {
    console.error('Falha de autenticação:', erro);
});

async function executar() {
    const arquivo = fs.createWriteStream(OUTPUT, {
        flags: 'wx',
        encoding: 'utf8'
    });

    let totalMensagens = 0;
    let totalComCampo = 0;
    let totalSemCampo = 0;
    let totalComEncaminhamentos = 0;
    let totalEncaminhamentos = 0;

    try {
        for (const canalConfig of canais) {
            console.log('\n======================================');
            console.log('CANAL:', canalConfig.nome);
            console.log('======================================');

            const canal = await client.getChatById(canalConfig.id);

            if (!canal) {
                throw new Error(
                    `Canal não encontrado: ${canalConfig.nome}`
                );
            }

            console.log('Buscando publicações disponíveis...');
            console.log('Limite solicitado:', LIMITE);

            const mensagens = await canal.fetchMessages({
                limit: LIMITE
            });

            console.log(
                'Publicações retornadas:',
                mensagens.length
            );

            let validas = 0;
            let ausentes = 0;
            let positivas = 0;
            let soma = 0;

            const ids = new Set();

            for (let i = 0; i < mensagens.length; i++) {
                const mensagem = mensagens[i];
                const raw = mensagem.rawData || {};

                const serialized = mensagem.id?._serialized;

                if (!serialized) {
                    ausentes++;
                    continue;
                }

                if (ids.has(serialized)) {
                    continue;
                }

                ids.add(serialized);

                const valor = raw.forwardsCount;

                const quantidade =
                    valor === null || valor === undefined
                        ? null
                        : Number(valor);

                const valido =
                    quantidade !== null &&
                    Number.isSafeInteger(quantidade) &&
                    quantidade >= 0;

                if (!valido) {
                    ausentes++;
                } else {
                    validas++;
                    soma += quantidade;

                    if (quantidade > 0) {
                        positivas++;
                    }
                }

                const rawId =
                    mensagem.id?.id ||
                    serialized.split('_').pop();

                const registro = {
                    canal_id: canalConfig.id,
                    canal_nome: canalConfig.nome,
                    mensagem_id: serialized,
                    message_id_raw: rawId,
                    timestamp: mensagem.timestamp || null,
                    encaminhamentos: valido ? quantidade : null,
                    campo_disponivel: valido
                };

                arquivo.write(
                    JSON.stringify(registro) + '\n'
                );

                if ((i + 1) % 250 === 0) {
                    console.log(
                        `Processadas ${i + 1}/${mensagens.length} publicações...`
                    );
                }
            }

            await new Promise((resolve, reject) => {
                arquivo.write('', err => {
                    if (err) reject(err);
                    else resolve();
                });
            });

            totalMensagens += ids.size;
            totalComCampo += validas;
            totalSemCampo += ausentes;
            totalComEncaminhamentos += positivas;
            totalEncaminhamentos += soma;

            console.log('\nResumo do canal:');
            console.log('Publicações únicas:', ids.size);
            console.log('Com forwardsCount:', validas);
            console.log('Sem campo válido:', ausentes);
            console.log('Com encaminhamentos positivos:', positivas);
            console.log('Soma dos encaminhamentos:', soma);
        }

    } finally {
        await new Promise((resolve, reject) => {
            arquivo.end(err => {
                if (err) reject(err);
                else resolve();
            });
        });
    }

    console.log('\n======================================');
    console.log('RESUMO GERAL');
    console.log('======================================');
    console.log('Publicações únicas:', totalMensagens);
    console.log('Com campo válido:', totalComCampo);
    console.log('Sem campo válido:', totalSemCampo);
    console.log('Com encaminhamentos positivos:', totalComEncaminhamentos);
    console.log('Soma dos encaminhamentos:', totalEncaminhamentos);
    console.log('\nArquivo:', OUTPUT);
}

client.once('ready', async () => {
    console.log('WhatsApp conectado!');

    try {
        await executar();
        console.log('\nColeta finalizada com sucesso.');
    } catch (erro) {
        console.error('\nERRO NA COLETA:', erro);
        process.exitCode = 1;
    } finally {
        finalizado = true;
        await client.destroy();
    }
});

client.initialize().catch(erro => {
    console.error('Erro ao inicializar WhatsApp:', erro);
    process.exitCode = 1;
});
