# -*- coding: utf-8 -*-
"""Comandos de voz da TV: roteamento e volume.

Cobrem bugs reais encontrados ao conectar uma TV de verdade:

- "aumenta o volume da tv" caía no volume do PC (o bloco VOLUME do parser
  rodava antes do bloco de CONECTIVIDADE);
- "coloca o volume da tv em 40%" era tratado como pedido de mídia;
- não havia como enviar vídeo/YouTube para a TV por voz.
"""
from core.actions.commands_parser import VegaCommandsParser as P
from core.brain.text_utils import normalize_text


def resolve(fala):
    return P._resolve_connectivity_request(normalize_text(fala))


def test_aumentar_volume_da_tv_vira_delta_positivo():
    assert resolve("aumenta o volume da tv") == ("volume", {"delta": 0.1})


def test_abaixar_volume_da_tv_vira_delta_negativo():
    assert resolve("abaixa o volume da tv") == ("volume", {"delta": -0.1})


def test_volume_da_tv_com_valor_explicito():
    assert resolve("coloca o volume da tv em 40%") == ("volume", {"level": 0.4})


def test_mutar_a_tv_leva_volume_a_zero():
    assert resolve("muta a tv") == ("volume", {"level": 0.0})


def test_pedido_de_video_na_tv_vai_para_youtube():
    action, kwargs = resolve("joga um video de gatos na tv")
    assert action == "play_youtube"
    # Palavras genéricas ("um", "video", artigos) são removidas da busca.
    assert kwargs.get("query") == "gatos"


def test_pedido_generico_de_midia_na_tv_pede_conteudo():
    assert resolve("toca musica na tv") == ("play_youtube", {})


def test_extrai_url_do_texto_original():
    # A URL é lida do texto ORIGINAL: a normalização remove ":" e "/".
    assert P._extract_media_url("joga https://example.com/video.mp4 na tv") == \
        "https://example.com/video.mp4"
    assert P._extract_media_url("toca uma musica") == ""


def test_comandos_de_volume_do_pc_nao_mencionam_tv():
    # Sem citar a TV, o volume continua sendo o do computador.
    assert resolve("aumenta o volume") is None
    assert resolve("abaixa o volume") is None