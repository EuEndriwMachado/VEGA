# Modelos Gemini selecionados

Fonte: tabela de limites fornecida pelo proprietario em 2026-09-18.

## Conversas normais e imagens

- Modelo principal: `gemini-3.5-flash-lite`
- Uso observado: 11/15 RPM, 14.14K/250K TPM, 73/500 RPD
- Fallback: `gemini-2.5-flash-lite`
- Uso observado do fallback: 0/10 RPM, 0/250K TPM, 0/20 RPD

## Modo Live

- Modelo: `gemini-2.5-flash-native-audio-preview-12-2025`
- Uso observado: 1 RPM, 28.31K/1M TPM, 3 RPD ilimitado

## Regra operacional

Nao trocar o modelo principal por `gemini-flash-latest` automaticamente: a tabela
mostrou que o alias estava consumindo o limite do Gemini 3.8 Flash. Se o modelo
principal retornar 429, a Vega tenta o fallback configurado.