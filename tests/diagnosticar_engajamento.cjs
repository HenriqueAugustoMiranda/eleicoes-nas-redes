const { Client, LocalAuth } = require('whatsapp-web.js');
const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..');

const canais = JSON.parse(
    fs.readFileSync(
        path.join(ROOT, 'config/canais.json'),
        'utf8'
    )
);

const client = new Client({
    authStrategy: new LocalAuth({
        clientId: 'canal-teste',
        dataPath: path.join(ROOT, '.wwebjs_auth')
    }),
    puppeteer: {
        headless: true
    }
});

function comTimeout(promessa, ms) {
    let timer;

    const limite = new Promise(resolve => {
        timer = setTimeout(
            () => resolve({ timeout: true }),
            ms
        );
    });

    return Promise.race([
        promessa,
        limite
    ]).finally(() => clearTimeout(timer));
}

console.log('[1/4] Iniciando conexão com WhatsApp Web...');

client.on('qr', qr => {
    console.log('\nQR Code necessário. Escaneie pelo WhatsApp:\n');

    require('qrcode-terminal').generate(qr, {
        small: true
    });
});

client.on('authenticated', () => {
    console.log('[2/4] Sessão autenticada.');
});

client.on('auth_failure', erro => {
    console.error('Falha de autenticação:', erro);
});

client.on('loading_screen', (percent, message) => {
    console.log(`Carregando WhatsApp: ${percent}% - ${message}`);
});

client.on('change_state', state => {
    console.log('Estado da conexão:', state);
});

client.on('disconnected', motivo => {
    console.log('WhatsApp desconectado:', motivo);
});

client.once('ready', async () => {
    console.log('[3/4] WhatsApp conectado!');
    console.log('Versão:', await client.getWWebVersion());

    try {
        for (const config of canais) {
            console.log('\n================================');
            console.log('CANAL:', config.nome);
            console.log('================================');

            const canal = await client.getChatById(config.id);

            console.log('Consultando últimas publicações...');

            const mensagens = await canal.fetchMessages({
                limit: 30
            });

            console.log('Publicações recuperadas:', mensagens.length);

            if (!mensagens.length) {
                console.log('Nenhuma publicação encontrada.');
                continue;
            }

            // ==========================================
            // INVESTIGAÇÃO DE ENCAMINHAMENTOS
            // ==========================================

            console.log('\nInvestigando encaminhamentos:');

            for (const mensagem of mensagens.slice(0, 5)) {
                const raw = mensagem.rawData || {};

                const camposRaw = Object.fromEntries(
                    Object.entries(raw).filter(
                        ([chave]) =>
                            /forward|share|count|reaction/i.test(chave)
                    )
                );

                console.dir({
                    id: mensagem.id?._serialized,
                    forwardingScore: mensagem.forwardingScore,
                    isForwarded: mensagem.isForwarded,
                    camposRaw
                }, { depth: 5 });
            }

            // ==========================================
            // INVESTIGAÇÃO DE REAÇÕES
            // ==========================================

            console.log('\nInvestigando reações:');

            const comReacoes = mensagens.filter(
                mensagem => mensagem.hasReaction === true
            );

            console.log(
                'Publicações com reações na amostra:',
                comReacoes.length
            );

            const amostras = comReacoes.slice(0, 3);

            if (!amostras.length) {
                console.log(
                    'Nenhuma publicação com reações encontrada.'
                );
            }

            for (const mensagem of amostras) {
                console.log('\nPublicação:', mensagem.id?._serialized);
                console.log('Tem reação:', mensagem.hasReaction);

                try {
                    console.log('Consultando getReactions()...');

                    const reacoes = await comTimeout(
                        Promise.resolve(mensagem.getReactions()),
                        15000
                    );

                    if (reacoes?.timeout) {
                        console.log(
                            'Consulta excedeu 15 segundos.'
                        );
                        continue;
                    }

                    console.dir({
                        retornoBruto: reacoes,
                        resumo: (reacoes || []).map(r => ({
                            emoji: r.aggregateEmoji || r.id,
                            quantidade: r.senders?.length ?? 0
                        }))
                    }, { depth: 6 });

                } catch (erro) {
                    console.log(
                        'Erro ao consultar reações:',
                        erro.message
                    );
                }
            }
        }

        console.log('\n[4/4] Diagnóstico finalizado.');

    } catch (erro) {
        console.error('Erro durante o diagnóstico:', erro);
    } finally {
        await client.destroy();
        console.log('Conexão encerrada.');
    }
});

client.initialize().catch(erro => {
    console.error('Erro ao iniciar o WhatsApp:', erro);
});
