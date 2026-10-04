# -*- coding: utf-8 -*-
"""Camada de conectividade da VEGA (celular, TV, dispositivos de rede).

Princípios de projeto:

1. **Nunca quebrar a VEGA.** Nenhum conector levanta exceção para fora:
   falhas viram mensagens claras em português.
2. **Degradação graciosa.** Cada conector informa se está disponível
   (ferramenta instalada) e, se não, o que instalar. Nada é obrigatório.
3. **Isolamento.** Esta camada não importa o núcleo da conversa; o núcleo
   é que a consulta. Adicionar um conector novo não altera o cérebro.
4. **Reaproveitamento.** O áudio (voz) continua sendo o `VegaSpeaker`
   existente — a conectividade só roteia a saída.

O registro de dispositivos é a fonte única de verdade sobre "o que existe
na rede do usuário", usada tanto pelo celular quanto pela TV.
"""

from core.connectivity.registry import DeviceRegistry, Device
from core.connectivity.base import Connector, ConnectorStatus
from core.connectivity.phone import PhoneConnector
from core.connectivity.tv import TVConnector
from core.connectivity.manager import ConnectivityManager

__all__ = [
    "DeviceRegistry",
    "Device",
    "Connector",
    "ConnectorStatus",
    "PhoneConnector",
    "TVConnector",
    "ConnectivityManager",
]