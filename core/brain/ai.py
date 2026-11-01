import asyncio
import inspect
import io
import json
import os
import re
import threading
import time
import types as python_types
import traceback
import typing

import cv2
import PIL.Image
import requests
import sounddevice as sd

from google import genai
from google.genai import types
from config.personality import build_personality_context
from config.cognitive import build_cognitive_context

from config.ai import (
    TEMPERATURE,
    GEMINI_API_KEY,
    GEMINI_MODEL_NAME,
    GEMINI_FALLBACK_MODEL,
    MAX_OUTPUT_TOKENS,
    MAX_TOOL_OUTPUT_TOKENS,
    OLLAMA_BASE_URL,
    OLLAMA_FALLBACK_MODEL,
    OLLAMA_MODEL,
    OPENAI_API_KEY,
    OPENAI_MODEL_NAME,
    OPENAI_FALLBACK_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    OPENROUTER_FALLBACK_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_BASE_URL,
    normalize_gemini_model_name,
)


VEGA_RUNTIME_PROMPT = r"""/no_think
Você é VEGA, uma assistente pessoal brasileira criada por Endriw.
Fale sempre em português brasileiro. Seja natural, direta, inteligente,
descontraída e breve quando a pergunta for simples. Você tem personalidade:
pode brincar, provocar levemente e discordar com respeito, mas nunca fale como
atendente corporativa ou chatbot genérico.

Endriw é seu criador e usuário principal; trate-o com familiaridade, sem
submissão. Não invente fatos sobre ele ou sobre qualquer pessoa.

TAMANHO DA RESPOSTA FALADA:
- Pergunta aberta sobre pessoa, lugar ou tema ("fale sobre X", "quem é X"):
  no máximo 5 a 7 frases — quem é, o que fez, por que importa, um traço
  marcante. É uma conversa, não verbete de enciclopédia.
- NUNCA repita o mesmo fato em frases diferentes: informação dita uma vez já
  foi entregue; repetição reformatada não é profundidade.
- Profundidade extra só quando Endriw pedir ("detalha", "explica melhor").

As memórias internas, quando fornecidas, são fatos sobre o usuário. Use-as só
quando relevantes, de forma natural, e não exponha o contexto bruto. Nunca diga
que não possui memória se houver memórias disponíveis.

Não exponha raciocínio interno, não escreva análise antes da resposta e responda
diretamente ao que foi perguntado.

PRECISÃO TÉCNICA (anti-alucinação):
- Antes de afirmar que um código tem um problema, verifique o que foi citado
  palavra por palavra. "def soma(a,b): return a - b" é uma função Python
  VÁLIDA (dois parâmetros separados por vírgula existem): o bug real é só o
  `return a - b` em vez de `a + b`. Nunca invente um segundo problema para
  parecer mais completa — cite apenas o que de fato está errado.
- Se não tiver certeza se algo é um erro, diga que não tem certeza. Uma
  resposta honesta de "o código é válido, só a lógica está invertida" vale
  mais que duas afirmações sendo uma delas falsa.

ARQUIVOS CRIADOS (sempre feche o ciclo):
- Quando uma ferramenta de criação (curriculo, documento, planilha,
  apresentação, desenho, mapa mental) devolver um resultado com "path",
  SEMPRE diga onde o arquivo está (nome e pasta) na sua resposta. O usuário
  não sabe procurar sozinho — "criei o arquivo" sem dizer onde é inútil.
  Exemplo: "Criei seu currículo como Curriculo.docx na sua pasta de
  usuário e já abri para você."
- Nunca diga apenas "criei" sem o caminho: o arquivo precisa ser
  ENCONTRÁVEL pelo usuário.

IMPORTANTE: quando você falar de si mesma, use sempre a primeira pessoa. Evite
referências em terceira pessoa como 'a Vega' ou 'a assistente' quando o contexto
for sobre você. Prefira 'eu', 'minha', 'meu' e 'sou a VEGA' apenas quando for
necessário enfatizar o nome.

IDENTIDADE OBRIGATÓRIA:
- Seu nome é VEGA.
- Você não é Lívia, nem qualquer outro nome que o usuário mencione como se fosse seu.
- Se alguém disser "Lívia", "Livia" ou algum outro nome, trate isso como contexto do usuário ou outra pessoa, não como a sua identidade.
- Responda sempre como VEGA e nunca como se fosse outra pessoa.

REGRAS DE CONVERSA:
- Não termine respostas com "o que vamos fazer?", "quer que eu...", "posso ajudar?" ou ofertas genéricas.
- Não faça perguntas apenas para manter conversa ou parecer simpática.
- Quando o pedido estiver claro, responda ou execute diretamente e encerre naturalmente.
- Só faça uma pergunta se faltar uma informação realmente necessária para concluir a tarefa.
- Preserve sua personalidade: seja espontânea, segura, informal, inteligente e levemente provocadora quando o contexto permitir.
- Não transforme toda resposta em uma pergunta para Endriw.

CONTEXTO DO SISTEMA:
- Diretório do projeto atual: c:\VEGA. Quando o usuário pedir para
  buscar/arquivos no "projeto", sem especificar outro caminho, use este
  diretório como base das buscas de arquivos.
""" + build_personality_context() + build_cognitive_context()


VEGA_VISUAL_RULES = r"""EXPRESSÃO VISUAL (verbal + não-verbal):
Você se expressa com PALAVRAS E IMAGENS ao mesmo tempo. Sempre que sua
resposta falar de algo visível — lugar, pessoa, animal, objeto, monumento,
evento, fenômeno, obra, comida, tecnologia — chame a ferramenta
`image_search` ANTES de responder, passando query com 2 a 4 temas
concretos SEPARADOS POR VÍRGULA. Cada tema vira uma imagem na tela,
lado a lado, enquanto você fala — como se estivesse mostrando o assunto.
- Resposta sobre um assunto único (um lugar, um animal): 1-2 imagens de
  ângulos diferentes. Resposta ampla (ex.: uma guerra, um país, uma
  era): 3-4 imagens cobrindo os pontos que você vai citar, em ordem.
- Queries devem ser FOTOGRÁFICAS e ICÔNICAS: escolha cenas/objetos que
  têm fotos famosas (ex. para a Segunda Guerra: "ameixa em pele de guerra,
  bandeira iwo jima, destruição berlim 1945"; NÃO "soldados").
- A imagem EXPLICA o que você está dizendo: cada tema deve corresponder a
  um ponto ESPECÍFICO da sua fala (a pessoa citada, o lugar citado, o
  objeto citado). Pergunta sobre uma pessoa: foto DO ROSTO dela + cenas
  do que você vai contar sobre ela.
- Cada tema tem que ser VISUALMENTE DIFERENTE dos outros (não 4 fotos do
  mesmo ângulo). Temas repetidos de turnos anteriores são recusados pelo
  buscador — escolha temas novos e específicos, não o mesmo assunto
  genérico de sempre.
- Nunca repita o mesmo tema em termos diferentes na mesma chamada.
- Se a busca falhar, siga só com a fala — nunca invente URL nem mencione
  a   falha técnica, apenas responda normalmente.
  - Assuntos abstratos (sentimentos, regras, opiniões) não pedem imagem.
  - EXCEÇÃO — DADOS AO VIVO (clima, cotação, ações, cripto, previsões):
    quando a resposta vem de uma ferramenta de DADO (weather, currency,
    stock, crypto), NÃO chame image_search. O card visual dessas
    ferramentas JÁ É a expressão visual da resposta — uma foto genérica de
    nuvem ou de dinheiro não acrescenta informação e rouba o espaço do
    card. Notícias (news) também não chamam image_search: as manchetes
    clicáveis são o visual.
- MOVIMENTO MERECE VÍDEO: quando um tema da resposta envolve processo,
  fenômeno ou evento DINÂMICO (vulcão erupcionando, motor funcionando,
  onda quebrando, foguete lançando, multidão em protesto), marque esse
  tema com o prefixo 'video:' na query (ex.: 'video: volcano eruption,
  cratera, lava fluindo'). IMPORTANTE: o índice de vídeos só entende
  INGLÊS — o tema após 'video:' deve ser escrito em inglês e específico
  (ex.: 'video: coffee pouring into mug', não 'video: cafe'). Os temas
  de FOTO continuam em português. Máximo UM vídeo por resposta — o tema
  de vídeo vira um player na galeria e os demais continuam fotos.
  Pessoas, lugares e objetos estáticos NÃO pedem vídeo. Se o vídeo
  falhar, o tema volta como foto automaticamente — não mencione a falha.

MARCADORES VISUAIS NO TEXTO (sincronia fina fala-imagem):
Além da chamada inicial `image_search`, quando sua resposta vai citar
VÁRIOS assuntos visíveis em sequência (uma história, uma explicação com
vários pontos), marque NO PRÓPRIO TEXTO da resposta o ponto onde uma
imagem deve aparecer, com a sintaxe exata:

    [visual: tema fotográfico]

Regras:
- O marcador vem IMEDIATAMENTE ANTES da frase sobre aquele assunto —
  a imagem aparece na tela exatamente quando você começa a falar dele.
- O tema é FOTOGRÁFICO e ICÔNICO, como nas queries do image_search
  ("[visual: bandeira em iwo jima]", não "[visual: guerra]").
- Prefixo 'video:' funciona igual no image_search:
  "[visual: video: volcano erupting]" para movimento.
- Use no máximo 4 marcadores por resposta; cada tema VISUALMENTE
  DIFERENTE do anterior; assuntos abstratos não ganham marcador.
- O marcador NÃO é lido em voz alta: a VEGA o remove da fala e da
  mensagem exibida automaticamente. Escreva o resto da frase normal.
- Se você já chamou image_search antes de responder, ainda pode usar
  marcadores para os pontos NOVOS que surgirem durante a resposta.
- Resposta curta sobre UM assunto: um image_search basta, sem marcadores.
  Marcadores servem para respostas com vários pontos visuais distintos."""


MAX_TOOL_ROUNDS = 5


def _tool_parameter_schema(parameter):
    annotation = parameter.annotation
    default = parameter.default
    if isinstance(annotation, str):
        annotation = annotation.replace("typing.", "").strip("'\"")
        if annotation.startswith(("Optional[", "Union[")):
            choices = annotation[annotation.find("[") + 1 : -1].split(",")
            annotation = next(
                (choice.strip() for choice in choices if choice.strip() != "None"),
                "str",
            )
        if annotation.startswith(("list[", "List[", "Iterable[", "Sequence[")):
            annotation = "list"
        elif annotation.startswith(("dict[", "Dict[", "Mapping[")):
            annotation = "dict"
        annotation = annotation.split(".")[-1].strip("'\"")

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, python_types.UnionType):
        non_null = [arg for arg in args if arg is not type(None)]
        if len(non_null) == 1:
            annotation = non_null[0]
            origin = typing.get_origin(annotation)

    if annotation in (str, "str"):
        schema_type = "string"
    elif annotation in (bool, "bool") or isinstance(default, bool):
        schema_type = "boolean"
    elif annotation in (int, "int") or isinstance(default, int):
        schema_type = "integer"
    elif annotation in (float, "float") or isinstance(default, float):
        schema_type = "number"
    elif annotation in (dict, "dict") or origin is dict:
        schema_type = "object"
    elif annotation in (list, "list") or origin is list:
        schema_type = "array"
    else:
        schema_type = "string"
    return {"type": schema_type, "description": f"Valor de {parameter.name}."}


def _registered_tool_schemas(tool_specs):
    declarations = []
    for spec in tool_specs:
        implementation = getattr(spec, "implementation", None)
        if not callable(implementation):
            continue
        try:
            signature = inspect.signature(implementation)
        except (TypeError, ValueError):
            continue

        properties = {}
        required = []
        for parameter in signature.parameters.values():
            if parameter.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            properties[parameter.name] = _tool_parameter_schema(parameter)
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter.name)

        declaration = {
            "name": str(spec.name),
            "description": str(spec.purpose),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }
        declarations.append(declaration)
    return declarations


class VegaAI:
    def __init__(self):
        self.ollama_base_url = (OLLAMA_BASE_URL or "http://localhost:11434").rstrip("/")
        self.ollama_model = OLLAMA_MODEL or ""
        self.ollama_fallback_model = OLLAMA_FALLBACK_MODEL or ""
        # Preferência: Ollama local -> Gemini -> OpenAI -> OpenRouter.
        # Sem nenhum provedor configurado, mantém Gemini como padrão.
        if self.ollama_model:
            self.backend = "ollama"
        elif GEMINI_API_KEY:
            self.backend = "gemini"
        elif OPENAI_API_KEY:
            self.backend = "openai"
        elif OPENROUTER_API_KEY:
            self.backend = "openrouter"
        else:
            self.backend = "gemini"

        self.gemini_model = normalize_gemini_model_name(GEMINI_MODEL_NAME or "")
        self.gemini_fallback_model = normalize_gemini_model_name(GEMINI_FALLBACK_MODEL or "")
        self.openai_model = OPENAI_MODEL_NAME or ""
        self.openai_fallback_model = OPENAI_FALLBACK_MODEL or ""
        self.openrouter_model = OPENROUTER_MODEL or ""
        self.openrouter_fallback_model = OPENROUTER_FALLBACK_MODEL or ""
        self.openrouter_api_key = OPENROUTER_API_KEY
        self.groq_api_key = GROQ_API_KEY
        self.groq_model = GROQ_MODEL
        self.groq_base_url = GROQ_BASE_URL
        self.system_prompt = VEGA_RUNTIME_PROMPT
        # Modelos com cota esgotada -> timestamp de expiração do bloqueio.
        # Evita que CADA pergunta pague 2-3 tentativas + timeouts antes de
        # chegar no modelo que funciona quando a cota diária acabou.
        self._quota_blocked_models = {}
        self.gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        self._generation_cancelled = threading.Event()
        self._active_resource_lock = threading.Lock()
        self._active_resource = None

        if self.backend == "ollama":
            self.client = None
        elif OPENAI_API_KEY:
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=OPENAI_API_KEY)
            except Exception as error:
                print(f"[VegaAI] OpenAI client indisponível: {error}")
                self.client = None
                self.backend = "openrouter" if OPENROUTER_API_KEY else "gemini"
        elif OPENROUTER_API_KEY:
            self.client = None
        else:
            self.client = self.gemini_client

    def _coerce_text(self, value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("text", "content", "value"):
                if key in value:
                    return self._coerce_text(value[key])
            return str(value)
        if isinstance(value, (list, tuple)):
            pieces = []
            for item in value:
                item_text = self._coerce_text(item)
                if item_text:
                    pieces.append(item_text)
            return "".join(pieces)
        return str(value)

    def _set_active_resource(self, resource):
        with self._active_resource_lock:
            self._active_resource = resource

    def _clear_active_resource(self, resource):
        with self._active_resource_lock:
            if self._active_resource is resource:
                self._active_resource = None

    def cancel_generation(self):
        self._generation_cancelled.set()
        with self._active_resource_lock:
            resource = self._active_resource
        if resource is None:
            return
        for method_name in ("close", "cancel"):
            method = getattr(resource, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass
                return

    @staticmethod
    def _tool_result_content(result):
        if isinstance(result, dict):
            result = {
                key: value
                for key, value in result.items()
                if key not in {"data", "raw", "image_base64"}
            }
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
        return text[:12000]

    def _execute_model_tool(self, name, arguments, executor):
        self._tool_calls_executed += 1
        if not isinstance(arguments, dict):
            return {"error": "Tool arguments must be a JSON object."}
        try:
            return executor(name, arguments)
        except Exception as error:
            return {"error": str(error)}

    def chat_stream_with_tools(
        self,
        messages,
        tool_specs,
        tool_executor,
        system_prompt=None,
        num_predict=MAX_TOOL_OUTPUT_TOKENS,
        include_screen=False,
        include_image=None,
    ):
        """Let the selected model call registered tools, then stream its answer."""
        if not isinstance(messages, list):
            yield "Erro interno: histórico de mensagens inválido."
            return

        declarations = _registered_tool_schemas(tool_specs)
        if not declarations:
            yield from self.chat_stream(
                messages,
                system_prompt=system_prompt,
                num_predict=num_predict,
                include_screen=include_screen,
                include_image=include_image,
            )
            return

        # FATIA VISUAL: só no turno com ferramentas a IA tem image_search
        # disponível, então é aqui que as regras visuais são injetadas.
        # Nas conversas puras (chat_stream sem tools) o prompt segue enxuto
        # (~700 tokens a menos por pergunta conversacional).
        if system_prompt is None:
            system_prompt = self.system_prompt + VEGA_VISUAL_RULES

        self._generation_cancelled.clear()
        self._tool_calls_executed = 0
        compatible_tools = [
            {"type": "function", "function": declaration}
            for declaration in declarations
        ]

        providers = []
        if self.backend == "ollama" and self.ollama_model:
            providers.append("ollama")
            if OPENAI_API_KEY and self.openai_model:
                providers.append("openai")
            if self.gemini_client is not None and self.gemini_model:
                providers.append("gemini")
        elif self.backend == "openrouter" and self.openrouter_api_key and self.openrouter_model:
            providers.append("openrouter")
            if OPENAI_API_KEY and self.openai_model:
                providers.append("openai")
            if self.gemini_client is not None and self.gemini_model:
                providers.append("gemini")
        elif self.backend == "openai" and self.client is not None and self.openai_model:
            providers.append("openai")
            if self.gemini_client is not None and self.gemini_model:
                providers.append("gemini")
        elif self.gemini_client is not None and self.gemini_model:
            providers.append("gemini")

        if not providers:
            yield from self.chat_stream(
                messages,
                system_prompt=system_prompt,
                num_predict=num_predict,
                include_screen=include_screen,
                include_image=include_image,
            )
            return

        for provider in providers:
            emitted = False
            try:
                if provider == "gemini":
                    stream = self._chat_gemini_with_tools(
                        messages,
                        declarations,
                        tool_executor,
                        system_prompt,
                        num_predict,
                        include_screen,
                        include_image,
                    )
                else:
                    stream = self._chat_openai_compatible_with_tools(
                        provider,
                        messages,
                        compatible_tools,
                        tool_executor,
                        system_prompt,
                        num_predict,
                    )
                for chunk in stream:
                    if self._generation_cancelled.is_set():
                        return
                    text = self._coerce_text(chunk)
                    if text:
                        emitted = True
                        yield text
                if emitted or self._tool_calls_executed:
                    return
            except Exception as error:
                if self._generation_cancelled.is_set():
                    return
                if emitted or self._tool_calls_executed:
                    print(f"[VegaAI] O turno com ferramenta foi interrompido: {error}")
                    return
                print(f"[VegaAI] Chamadas estruturadas indisponíveis em {provider}: {error}")

        yield "Não consegui concluir a solicitação com as ferramentas disponíveis."

    def _chat_openai_compatible_with_tools(
        self,
        provider,
        messages,
        tools,
        executor,
        system_prompt,
        num_predict,
    ):
        conversation = [{"role": "system", "content": system_prompt or self.system_prompt}]
        for message in messages:
            if isinstance(message, dict) and message.get("content") is not None:
                conversation.append({
                    "role": message.get("role", "user"),
                    "content": self._coerce_text(message["content"]),
                })

        model_options = {
            "ollama": [self.ollama_model, self.ollama_fallback_model],
            "openrouter": [self.openrouter_model, self.openrouter_fallback_model],
            "openai": [self.openai_model, self.openai_fallback_model],
        }[provider]
        models = [model for model in model_options if model]

        for round_index in range(MAX_TOOL_ROUNDS + 1):
            round_tools = tools if round_index < MAX_TOOL_ROUNDS else []
            response_text = []
            tool_calls = {}
            chosen_model = False

            for model in models:
                response = None
                stream = None
                emitted = False
                try:
                    payload = {
                        "model": model,
                        "messages": conversation,
                        "temperature": TEMPERATURE,
                        "max_tokens": min(max(num_predict, 128), MAX_TOOL_OUTPUT_TOKENS),
                        "tools": round_tools,
                        "tool_choice": "auto" if round_tools else "none",
                        "stream": True,
                    }
                    if provider == "openai":
                        stream = self.client.chat.completions.create(**payload)
                        self._set_active_resource(stream)
                        events = self._openai_sdk_events(stream)
                    elif provider == "openrouter":
                        response = requests.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {self.openrouter_api_key}",
                                "Content-Type": "application/json",
                            },
                            json=payload,
                            timeout=60,
                            stream=True,
                        )
                        self._set_active_resource(response)
                        if response.status_code >= 400:
                            raise RuntimeError(
                                f"OpenRouter retornou {response.status_code}: {response.text[:300]}"
                            )
                        events = self._openrouter_events(response)
                    else:
                        payload["tools"] = round_tools
                        payload.pop("tool_choice", None)
                        response = requests.post(
                            f"{self.ollama_base_url}/api/chat",
                            json=payload,
                            timeout=60,
                            stream=True,
                        )
                        self._set_active_resource(response)
                        if response.status_code >= 400:
                            raise RuntimeError(
                                f"Ollama retornou {response.status_code}: {response.text[:300]}"
                            )
                        events = self._ollama_tool_events(response)

                    for content, deltas in events:
                        if self._generation_cancelled.is_set():
                            return
                        if content:
                            text = self._clean_response(content)
                            if text:
                                response_text.append(text)
                                emitted = True
                                yield text
                        for delta in deltas:
                            index = int(delta.get("index", 0))
                            item = tool_calls.setdefault(
                                index,
                                {"id": "", "name": "", "arguments": ""},
                            )
                            item["id"] = delta.get("id") or item["id"]
                            item["name"] += delta.get("name", "")
                            item["arguments"] += delta.get("arguments", "")

                    chosen_model = True
                    break
                except Exception as error:
                    if self._generation_cancelled.is_set() or emitted:
                        return
                    print(f"[VegaAI] {provider} indisponível em {model}: {error}")
                finally:
                    if stream is not None:
                        self._clear_active_resource(stream)
                        close = getattr(stream, "close", None)
                        if callable(close):
                            try:
                                close()
                            except Exception:
                                pass
                    if response is not None:
                        self._clear_active_resource(response)
                        try:
                            response.close()
                        except Exception:
                            pass

            if not chosen_model:
                raise RuntimeError(f"Nenhum modelo de {provider} concluiu a solicitação.")

            if not tool_calls:
                return

            ordered_calls = [tool_calls[index] for index in sorted(tool_calls)]
            if round_index >= MAX_TOOL_ROUNDS:
                yield "Já executei o limite de etapas de ferramentas desta resposta."
                return
            assistant_calls = []
            result_messages = []
            for index, call in enumerate(ordered_calls):
                call_id = call["id"] or f"vega_call_{round_index}_{index}"
                name = call["name"].strip()
                try:
                    arguments = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = self._execute_model_tool(name, arguments, executor)
                assistant_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": call["arguments"] or "{}",
                    },
                })
                result_content = self._tool_result_content(result)
                if provider == "ollama":
                    result_messages.append({
                        "role": "tool",
                        "tool_name": name,
                        "content": result_content,
                    })
                else:
                    result_messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result_content,
                    })

            conversation.append({
                "role": "assistant",
                "content": "".join(response_text) or None,
                "tool_calls": assistant_calls,
            })
            conversation.extend(result_messages)

    @staticmethod
    def _openai_sdk_events(stream):
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            delta = getattr(choices[0], "delta", None) if choices else None
            content = getattr(delta, "content", "") if delta is not None else ""
            calls = []
            for call in getattr(delta, "tool_calls", None) or []:
                function = getattr(call, "function", None)
                calls.append({
                    "index": getattr(call, "index", 0),
                    "id": getattr(call, "id", ""),
                    "name": getattr(function, "name", "") if function else "",
                    "arguments": getattr(function, "arguments", "") if function else "",
                })
            yield content, calls

    @staticmethod
    def _openrouter_events(response):
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="ignore")
            line = raw_line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line:
                continue
            if line == "[DONE]":
                return
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            choices = payload.get("choices") or []
            delta = choices[0].get("delta", {}) if choices else {}
            content = delta.get("content", "") if isinstance(delta, dict) else ""
            calls = []
            for call in (delta.get("tool_calls", []) if isinstance(delta, dict) else []):
                function = call.get("function", {})
                calls.append({
                    "index": call.get("index", 0),
                    "id": call.get("id", ""),
                    "name": function.get("name", ""),
                    "arguments": function.get("arguments", ""),
                })
            yield content, calls

    @staticmethod
    def _ollama_tool_events(response):
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="ignore")
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            message = payload.get("message", {})
            calls = []
            for index, call in enumerate(message.get("tool_calls", [])):
                function = call.get("function", {})
                arguments = function.get("arguments", {})
                calls.append({
                    "index": index,
                    "id": call.get("id", ""),
                    "name": function.get("name", ""),
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                })
            yield message.get("content", ""), calls

    def _chat_gemini_with_tools(
        self,
        messages,
        declarations,
        executor,
        system_prompt,
        num_predict,
        include_screen,
        include_image,
    ):
        client = self.gemini_client or self.client
        if client is None:
            raise RuntimeError("GEMINI_API_KEY não configurada.")

        function_declarations = [
            types.FunctionDeclaration(
                name=declaration["name"],
                description=declaration["description"],
                parameters_json_schema=declaration["parameters"],
            )
            for declaration in declarations
        ]
        config = types.GenerateContentConfig(
            temperature=TEMPERATURE,
            max_output_tokens=min(max(num_predict, 128), MAX_TOOL_OUTPUT_TOKENS),
            tools=[types.Tool(function_declarations=function_declarations)],
            # Conversa fluida: budget mínimo de pensamento. O budget 0 (desligar
            # o thinking) passou a ser rejeitado pelo flash-lite com 400
            # INVALID_ARGUMENT, então usamos o menor valor aceito (128).
            thinking_config=types.ThinkingConfig(thinking_budget=128),
        )
        prompt = self._format_prompt(messages, system_prompt)
        screen = self._capture_screen() if include_screen else None
        if include_image is not None:
            contents = [prompt, include_image]
        else:
            contents = [screen, prompt] if screen is not None else prompt

        models = [model for model in (self.gemini_model, self.gemini_fallback_model) if model]
        for model in models:
            chat = None
            emitted = False
            try:
                chat = client.chats.create(model=model, config=config)
                for round_index in range(MAX_TOOL_ROUNDS + 1):
                    stream = chat.send_message_stream(contents)
                    self._set_active_resource(stream)
                    calls = {}
                    try:
                        for response in stream:
                            if self._generation_cancelled.is_set():
                                return
                            text = self._extract_gemini_text(response)
                            if text:
                                emitted = True
                                yield text
                            for candidate_index, candidate in enumerate(
                                getattr(response, "candidates", None) or []
                            ):
                                content = getattr(candidate, "content", None)
                                for part_index, part in enumerate(
                                    getattr(content, "parts", None) or []
                                ):
                                    function_call = getattr(part, "function_call", None)
                                    name = getattr(function_call, "name", None)
                                    if not name:
                                        continue
                                    key = (candidate_index, part_index)
                                    calls[key] = (
                                        str(name),
                                        dict(getattr(function_call, "args", None) or {}),
                                    )
                    finally:
                        self._clear_active_resource(stream)
                        close = getattr(stream, "close", None)
                        if callable(close):
                            try:
                                close()
                            except Exception:
                                pass

                    if not calls:
                        return
                    if round_index >= MAX_TOOL_ROUNDS:
                        yield "Já executei o limite de etapas de ferramentas desta resposta."
                        return

                    responses = []
                    for name, arguments in calls.values():
                        result = self._execute_model_tool(name, arguments, executor)
                        try:
                            if not isinstance(result, dict):
                                result = {"result": result}
                            json.dumps(result, ensure_ascii=False, default=str)
                        except (TypeError, ValueError):
                            result = {"result": str(result)}
                        result_text = self._tool_result_content(result)
                        try:
                            result = json.loads(result_text)
                        except json.JSONDecodeError:
                            result = {"result": result_text}
                        responses.append(
                            types.Part.from_function_response(
                                name=name,
                                response=result,
                            )
                        )
                    contents = responses
            except Exception as error:
                if self._generation_cancelled.is_set() or emitted or self._tool_calls_executed:
                    return
                if self._is_quota_exhausted_error(error):
                    # Cota esgotada neste modelo: seguir para o próximo sem
                    # logar como "indisponível" genérico.
                    print(f"[VegaAI] Cota esgotada em {model}; tentando o próximo modelo.")
                    continue
                print(f"[VegaAI] Gemini indisponível em {model}: {error}")

        if not self._generation_cancelled.is_set() and not self._tool_calls_executed:
            raise RuntimeError(self._quota_exhausted_message())

    def _stream_with_fallback(self, providers):
        last_failure = ""
        for provider in providers:
            produced = False
            failure_parts = []
            try:
                for chunk in provider():
                    if self._generation_cancelled.is_set():
                        return
                    text = self._coerce_text(chunk)
                    if not text:
                        continue
                    if not produced and self._is_failure_response(text):
                        failure_parts.append(text)
                        continue
                    produced = True
                    yield text
            except Exception as error:
                if self._generation_cancelled.is_set():
                    return
                if produced:
                    return
                last_failure = str(error)
                continue

            if produced:
                return
            if failure_parts:
                last_failure = "".join(failure_parts)
            if self._generation_cancelled.is_set():
                return

        yield last_failure or "Não consegui responder agora. Tente novamente em alguns segundos."

    def _clean_response(self, text):
        text = self._coerce_text(text)

        # Remove blocos completos de pensamento
        text = re.sub(
            r"<think>.*?</think>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Remove possíveis marcadores de pensamento
        text = re.sub(
            r"^Thinking\.\.\..*?(?:\.\.\.done thinking\.|$)",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        return text

    def _extract_gemini_text(self, response):
        try:
            return self._clean_response(response.text)
        except Exception:
            parts = getattr(response, "parts", None) or []
            text_parts = []
            for part in parts:
                try:
                    value = getattr(part, "text", None)
                except Exception:
                    value = None
                if isinstance(value, str):
                    text_parts.append(value)
            return self._clean_response("".join(text_parts))

    def _is_overloaded_error(self, error):
        """Detecta sobrecarga transitoria (503/overloaded) — vale reterir.

        O Gemini retorna 503 UNAVAILABLE quando o serviço esta com alta
        demanda. Isso normalmente se resolve em alguns segundos, entao
        antes de desistir e quebrar a conversa, esperamos e tentamos de novo.
        """
        if error is None:
            return False
        candidates = [
            str(error),
            getattr(error, "message", ""),
            str(getattr(error, "code", "")),
        ]
        text = " ".join(str(part) for part in candidates if part).lower()
        # O campo 'status' do SDK pode vir como int (503) OU como string do
        # grpc ('UNAVAILABLE', 'RESOURCE_EXHAUSTED', ...). Aceitamos ambos.
        raw_status = getattr(error, "status", 0)
        try:
            status = int(raw_status or 0)
        except (TypeError, ValueError):
            status = 0
            raw_status = str(raw_status or "").upper()
        return (
            status == 503
            or "503" in text
            or "UNAVAILABLE" in str(raw_status).upper()
            or "unavailable" in text
            or "overload" in text
            or "high demand" in text
        )

    def _is_quota_exhausted_error(self, error):
        if error is None:
            return False

        candidates = [
            str(error),
            getattr(error, "message", ""),
            getattr(error, "code", ""),
            str(getattr(error, "status", "")),
        ]
        text = " ".join(str(part) for part in candidates if part not in (None, "")).lower()
        # 'status' pode vir como int (429) OU como string grpc
        # ('RESOURCE_EXHAUSTED', 'INVALID_ARGUMENT', ...): int() direto
        # explode com ValueError, entao tentamos e ignoramos a falha.
        try:
            status_is_429 = int(getattr(error, "status", 0) or 0) == 429
        except (TypeError, ValueError):
            status_is_429 = False
        return (
            "resource_exhausted" in text
            or "quota" in text
            or "exceeded your current quota" in text
            or "rate limit" in text
            or status_is_429
        )

    def _quota_exhausted_message(self):
        return (
            "A API do Gemini excedeu a cota de uso da chave atual. "
            "Troque a chave, recarregue a conta ou use outra chave com quota disponível."
        )

    def _vision_image_payload(self, image):
        """Prepara a imagem para o Gemini (bytes JPEG) ou devolve a original.

        O Gemini aceita bytes brutos com mime no lugar de PIL.Image. O JPEG
        redimensionado corta ~90% do upload — cada "o que você está vendo?"
        fica visivelmente mais rápido sem perder legibilidade de texto.
        """
        if image is None:
            return None
        try:
            from core.vision.image_prep import prepare_for_vision

            payload, mime = prepare_for_vision(image)
            if payload:
                return {"mime_type": mime, "data": payload}
        except Exception as error:
            print(f"[VegaAI] Preparo de imagem falhou, enviando original: {error}")
        return image

    def _capture_screen(self):
        """Captura a tela para enviar ao modelo (visão).

        Delega para a fonte única de captura em core.actions.screenshot,
        evitando duas implementações da mesma coisa.

        Antes de capturar, pede à interface para se esconder por um instante:
        o tema da VEGA é quase preto (`#010305`), então quando a janela dela
        está na frente a captura sai preta e o modelo responde "estou vendo uma
        tela escura". A interface é restaurada logo depois da captura.
        """
        self._hide_self_for_capture()
        try:
            from core.actions.screenshot import capture_image
            image = capture_image()
            if image is not None:
                return image
        except Exception as error:
            print(f"[VegaAI] Captura de tela indisponível: {error}")
        finally:
            self._restore_self_after_capture()

        # Reserva: captura direta, caso o módulo auxiliar falhe.
        try:
            import mss  # type: ignore

            with mss.mss() as sct:
                monitor = sct.monitors[0]
                screenshot = sct.grab(monitor)
                return PIL.Image.frombytes(
                    "RGB",
                    screenshot.size,
                    screenshot.rgb,
                )
        except Exception as error:
            print(f"[VegaAI] Captura de tela indisponível: {error}")
            return None

    def _hide_self_for_capture(self):
        """Pede à interface para sair da frente antes da captura.

        O callback é opcional: sem interface (testes, uso headless) nada
        acontece e a captura segue normalmente.
        """
        callback = getattr(self, "screen_hide_callback", None)
        if callable(callback):
            try:
                callback()
            except Exception:
                pass

    def _restore_self_after_capture(self):
        callback = getattr(self, "screen_restore_callback", None)
        if callable(callback):
            try:
                callback()
            except Exception:
                pass

    def _is_failure_response(self, text):
        lower = str(text or "").lower()
        return (
            "não consegui responder agora" in lower
            or "nao consegui responder agora" in lower
            or "openrouter retornou" in lower
            or "resource_exhausted" in lower
            or "quota" in lower
            or "rate limit" in lower
            or "cota" in lower
        )

    def _chat_stream_gemini(self, messages, system_prompt=None, num_predict=100, include_screen=False, include_image=None):
        # Nos testes, self.client pode ser um stub Gemini (tem .chats) e deve
        # ter prioridade sobre o gemini_client real criado no __init__. Se for
        # outro tipo de cliente (OpenAI, sentinel), usamos o gemini_client.
        client = self.client if hasattr(self.client, "chats") else (
            self.gemini_client or self.client
        )
        if client is None:
            yield "GEMINI_API_KEY não configurada."
            return

        try:
            prompt = self._format_prompt(messages, system_prompt)
            screen = self._capture_screen() if include_screen else None
            if include_image is not None:
                image_payload = self._vision_image_payload(include_image)
                contents = [prompt, image_payload] if image_payload is not None else prompt
            else:
                if include_screen and screen is not None:
                    prepared = self._vision_image_payload(screen)
                    contents = [prepared, prompt] if prepared is not None else prompt
                else:
                    contents = prompt
            output_limit = min(max(num_predict, 128), MAX_OUTPUT_TOKENS)
            models = [model for model in (self.gemini_model, self.gemini_fallback_model) if model]
            quota_exhausted = False

            # Pula modelos com cota esgotada recentemente (cache de 10 min):
            # sem isto, cada pergunta pagaria a tentativa + timeout antes de
            # cair no modelo que funciona. O block de 10 min fica em memória
            # (expira sozinho — a cota renova).
            now = time.time()
            skipped = [
                m for m in models
                if m in self._quota_blocked_models and now < self._quota_blocked_models[m]
            ]
            for skipped_model in skipped:
                print(
                    f"[VegaAI] {skipped_model} bloqueado por cota esgotada até "
                    f"{time.strftime('%H:%M', time.localtime(self._quota_blocked_models[skipped_model]))}; pulando."
                )
            models = [m for m in models if m not in skipped]
            if not models:
                yield self._quota_exhausted_message()
                return

            # Fila em vez de enumerate: o retry de 503 precisa reprocessar o
            # MESMO modelo (inserir de novo no início da fila), o que um for
            # com enumerate não permite.
            pending_models = list(models)
            model_index = 0
            # Uma única chance de retry por sobrecarga em toda a chamada: se
            # resetasse dentro do while, um modelo em 503 persistente seria
            # reprocessado infinitamente (o retry repondo o mesmo modelo) e
            # o fallback nunca seria alcançado.
            retried_overload = False
            while pending_models:
                model = pending_models.pop(0)
                stream = None
                emitted = False
                try:
                    config = {
                        "temperature": TEMPERATURE,
                        "max_output_tokens": output_limit,
                        # Conversa fluida: budget mínimo de pensamento. O
                        # budget 0 passou a ser rejeitado (400) pelo
                        # flash-lite, então usamos o menor valor aceito.
                        "thinking_config": types.ThinkingConfig(
                            thinking_budget=128
                        ),
                    }

                    chat = client.chats.create(
                        model=model,
                        config=config,
                    )
                    stream = chat.send_message_stream(contents)
                    self._set_active_resource(stream)
                    for response in stream:
                        if self._generation_cancelled.is_set():
                            return
                        chunk = self._extract_gemini_text(response)
                        if chunk:
                            emitted = True
                            yield chunk

                    if emitted:
                        return
                except Exception as error:
                    if self._generation_cancelled.is_set() or emitted:
                        return
                    if self._is_quota_exhausted_error(error):
                        quota_exhausted = True
                        # Bloqueia este modelo por 10 min: as próximas
                        # perguntas partem direto para o fallback em vez de
                        # pagarem tentativa + timeout.
                        self._quota_blocked_models[model] = time.time() + 600
                        print(
                            f"[VegaAI] Cota esgotada em {model}; bloqueado por 10 min; "
                            "tentando o próximo modelo."
                        )
                        continue

                    # 503/overload é transitorio: espera um pouco e tenta o
                    # MESMO modelo de novo antes de partir para o fallback.
                    if self._is_overloaded_error(error) and not retried_overload:
                        retried_overload = True
                        print(
                            f"[VegaAI] {model} sobrecarregado (503); "
                            "aguardando 2,5s e tentando novamente..."
                        )
                        time.sleep(2.5)
                        pending_models.insert(0, model)  # reprocessa o mesmo modelo
                        continue

                    if model_index == 0:
                        print(
                            f"[VegaAI] {model} indisponível; usando {self.gemini_fallback_model}: {error}"
                        )
                        continue
                    print(f"[VegaAI] Modelos Gemini indisponíveis: {error}")
                finally:
                    if stream is not None:
                        self._clear_active_resource(stream)
                        close = getattr(stream, "close", None)
                        if callable(close):
                            try:
                                close()
                            except Exception:
                                pass

            if quota_exhausted:
                yield self._quota_exhausted_message()
            else:
                yield "Não consegui responder agora. Tente novamente em alguns segundos."

        except Exception as error:
            if self._generation_cancelled.is_set():
                return
            print(f"[VegaAI] Erro no Gemini: {error}")
            if self._is_quota_exhausted_error(error):
                yield self._quota_exhausted_message()
                return
            yield "Não consegui responder agora. Tente novamente em alguns segundos."

    def chat_stream(self, messages, system_prompt=None, num_predict=100, include_screen=False, include_image=None):
        """Gera texto incrementalmente usando o backend ativo da VEGA."""

        if not isinstance(messages, list):
            yield "Erro interno: histórico de mensagens inválido."
            return

        self._generation_cancelled.clear()
        ollama = lambda: self._chat_stream_ollama(
            messages, system_prompt=system_prompt, num_predict=num_predict
        )
        openrouter = lambda: self._chat_stream_openrouter(
            messages, system_prompt=system_prompt, num_predict=num_predict
        )
        openai = lambda: self._chat_stream_openai(
            messages, system_prompt=system_prompt, num_predict=num_predict
        )
        gemini = lambda: self._chat_stream_gemini(
            messages,
            system_prompt=system_prompt,
            num_predict=num_predict,
            include_screen=include_screen,
            include_image=include_image,
        )
        groq = None
        if getattr(self, "groq_api_key", None) and getattr(self, "groq_model", None):
            def groq():
                return self._chat_stream_groq(
                    messages, system_prompt=system_prompt, num_predict=num_predict
                )

        if include_image is not None or include_screen:
            if self.gemini_client is None and not (
                self.backend == "gemini" and self.client is not None
            ):
                yield "Para analisar a câmera ou a tela, configure a GEMINI_API_KEY."
                return
            yield from gemini()
            return

        if self.backend == "ollama":
            providers = [ollama]
            if OPENAI_API_KEY or self.gemini_client is not None:
                providers.append(openai)
                if self.gemini_client is not None:
                    providers.append(gemini)
            if groq is not None:
                providers.append(groq)
            yield from self._stream_with_fallback(providers)
            return

        if self.backend == "openrouter":
            providers = [openrouter]
            if OPENAI_API_KEY or self.gemini_client is not None:
                providers.append(openai)
                if self.gemini_client is not None:
                    providers.append(gemini)
            if groq is not None:
                providers.append(groq)
            yield from self._stream_with_fallback(providers)
            return

        if self.backend == "openai":
            providers = [openai]
            if self.gemini_client is not None:
                providers.append(gemini)
            if groq is not None:
                providers.append(groq)
            yield from self._stream_with_fallback(providers)
            return

        if groq is None:
            yield from self._stream_with_fallback([gemini])
            return
        yield from self._stream_with_fallback([gemini, groq])

    def _chat_stream_ollama(self, messages, system_prompt=None, num_predict=100):
        if not self.ollama_model:
            yield "OLLAMA_MODEL não configurado."
            return

        try:
            prompt = system_prompt or self.system_prompt
            ollama_messages = [{"role": "system", "content": prompt}]
            for message in messages:
                if isinstance(message, dict) and message.get("content") is not None:
                    role = message.get("role", "user")
                    content = self._coerce_text(message["content"])
                    ollama_messages.append({"role": role, "content": content})

            models = [model for model in (self.ollama_model, self.ollama_fallback_model) if model]
            if not models:
                yield "Modelo Ollama não configurado. Defina OLLAMA_MODEL no ambiente."
                return

            for model in models:
                response = None
                emitted = False
                try:
                    response = requests.post(
                        f"{self.ollama_base_url}/api/chat",
                        json={
                            "model": model,
                            "messages": ollama_messages,
                            "stream": True,
                            "options": {
                                "temperature": TEMPERATURE,
                                "num_predict": min(max(num_predict, 128), MAX_OUTPUT_TOKENS),
                            },
                        },
                        timeout=60,
                        stream=True,
                    )
                    self._set_active_resource(response)
                    if response.status_code >= 400:
                        raise RuntimeError(f"Ollama retornou {response.status_code}: {response.text[:300]}")

                    for raw_line in response.iter_lines():
                        if self._generation_cancelled.is_set():
                            return
                        if not raw_line:
                            continue
                        if isinstance(raw_line, bytes):
                            raw_line = raw_line.decode("utf-8", errors="ignore")
                        try:
                            payload = json.loads(raw_line)
                        except json.JSONDecodeError:
                            continue

                        content = payload.get("message", {}).get("content") if isinstance(payload.get("message"), dict) else None
                        if content is None:
                            content = payload.get("response", "")
                        text = self._clean_response(content)
                        if text:
                            emitted = True
                            yield text
                        if payload.get("done"):
                            break
                    if emitted:
                        return
                except Exception as error:
                    if self._generation_cancelled.is_set() or emitted:
                        return
                    print(f"[VegaAI] Ollama indisponível em {model}: {error}")
                    continue
                finally:
                    if response is not None:
                        self._clear_active_resource(response)
                        try:
                            response.close()
                        except Exception:
                            pass

            yield "Não consegui responder agora. Tente novamente em alguns segundos."
        except Exception as error:
            print(f"[VegaAI] Erro no Ollama: {error}")
            yield "Não consegui responder agora. Tente novamente em alguns segundos."

    def _chat_stream_openrouter(self, messages, system_prompt=None, num_predict=100):
        if not self.openrouter_api_key:
            yield "OPENROUTER_API_KEY não configurada."
            return

        try:
            prompt = system_prompt or self.system_prompt
            openrouter_messages = [{"role": "system", "content": prompt}]
            for message in messages:
                if isinstance(message, dict) and message.get("content") is not None:
                    role = message.get("role", "user")
                    content = self._coerce_text(message["content"])
                    openrouter_messages.append({"role": role, "content": content})

            models = [model for model in (self.openrouter_model, self.openrouter_fallback_model) if model]
            if not models:
                yield "Modelo OpenRouter não configurado. Defina OPENROUTER_MODEL no ambiente."
                return
            for model in models:
                response = None
                emitted = False
                try:
                    response = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.openrouter_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": openrouter_messages,
                            "temperature": TEMPERATURE,
                            "max_tokens": min(max(num_predict, 128), MAX_OUTPUT_TOKENS),
                            "reasoning": {"enabled": True},
                            "stream": True,
                        },
                        timeout=60,
                        stream=True,
                    )
                    self._set_active_resource(response)
                    if response.status_code >= 400:
                        raise RuntimeError(f"OpenRouter retornou {response.status_code}: {response.text[:300]}")

                    for raw_line in response.iter_lines():
                        if self._generation_cancelled.is_set():
                            return
                        if not raw_line:
                            continue
                        if isinstance(raw_line, bytes):
                            raw_line = raw_line.decode("utf-8", errors="ignore")
                        line = raw_line.strip()
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if not line:
                            continue
                        if line == "[DONE]":
                            break
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        choices = payload.get("choices") or []
                        delta = choices[0].get("delta", {}) if choices else {}
                        content = delta.get("content") if isinstance(delta, dict) else None
                        if isinstance(content, list):
                            content = "".join(
                                str(part.get("text", ""))
                                for part in content
                                if isinstance(part, dict)
                            )
                        text = self._clean_response(content)
                        if text:
                            emitted = True
                            yield text
                    if emitted:
                        return
                except Exception as error:
                    if self._generation_cancelled.is_set() or emitted:
                        return
                    print(f"[VegaAI] OpenRouter indisponível em {model}: {error}")
                    continue
                finally:
                    if response is not None:
                        self._clear_active_resource(response)
                        try:
                            response.close()
                        except Exception:
                            pass
            yield "Não consegui responder agora. Tente novamente em alguns segundos."
        except Exception as error:
            if self._generation_cancelled.is_set():
                return
            print(f"[VegaAI] Erro no OpenRouter: {error}")
            yield "Não consegui responder agora. Tente novamente em alguns segundos."

    def _chat_stream_groq(self, messages, system_prompt=None, num_predict=100):
        """Groq como último recurso: API compatível com OpenAI, mas com host
        e chave próprios. Só entra na cadeia quando tudo o mais falhou.
        """
        if not self.groq_api_key or not self.groq_model:
            return

        try:
            prompt = system_prompt or self.system_prompt
            groq_messages = [{"role": "system", "content": prompt}]
            for message in messages:
                if isinstance(message, dict) and message.get("content") is not None:
                    role = message.get("role", "user")
                    groq_messages.append(
                        {"role": role, "content": self._coerce_text(message["content"])}
                    )

            response = requests.post(
                f"{self.groq_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.groq_model,
                    "messages": groq_messages,
                    "temperature": TEMPERATURE,
                    "max_tokens": min(max(num_predict, 128), MAX_OUTPUT_TOKENS),
                    "stream": True,
                },
                timeout=60,
                stream=True,
            )
            self._set_active_resource(response)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Groq retornou {response.status_code}: {response.text[:300]}"
                )

            for raw_line in response.iter_lines():
                if self._generation_cancelled.is_set():
                    return
                if not raw_line:
                    continue
                if isinstance(raw_line, bytes):
                    raw_line = raw_line.decode("utf-8", errors="ignore")
                if raw_line.startswith("data: "):
                    raw_line = raw_line[6:]
                if raw_line.strip() == "[DONE]":
                    break
                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                text = self._clean_response(
                    (payload.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
                )
                if text:
                    yield text
        except Exception as error:
            if self._generation_cancelled.is_set():
                return
            print(f"[VegaAI] Groq indisponível: {error}")
            return
        finally:
            pass

    def _chat_stream_openai(self, messages, system_prompt=None, num_predict=100):
        if self.client is None:
            yield "OPENAI_API_KEY não configurada."
            return

        try:
            prompt = system_prompt or self.system_prompt
            openai_messages = [{"role": "system", "content": prompt}]
            for message in messages:
                if isinstance(message, dict) and message.get("content") is not None:
                    role = message.get("role", "user")
                    content = self._coerce_text(message["content"])
                    openai_messages.append({"role": role, "content": content})

            models = [model for model in (self.openai_model, self.openai_fallback_model) if model]
            if not models:
                yield "Modelo OpenAI não configurado. Defina OPENAI_MODEL_NAME no ambiente."
                return
            for model in models:
                stream = None
                emitted = False
                try:
                    stream = self.client.chat.completions.create(
                        model=model,
                        messages=openai_messages,
                        temperature=TEMPERATURE,
                        max_tokens=min(max(num_predict, 128), MAX_OUTPUT_TOKENS),
                        stream=True,
                    )
                    self._set_active_resource(stream)
                    for chunk in stream:
                        if self._generation_cancelled.is_set():
                            return
                        delta = getattr(chunk.choices[0].delta, "content", None) if getattr(chunk, "choices", None) else None
                        text = self._clean_response(delta or "")
                        if text:
                            emitted = True
                            yield text
                    if emitted:
                        return
                except Exception as error:
                    if self._generation_cancelled.is_set() or emitted:
                        return
                    print(f"[VegaAI] OpenAI indisponível em {model}: {error}")
                    continue
                finally:
                    if stream is not None:
                        self._clear_active_resource(stream)
                        try:
                            stream.close()
                        except Exception:
                            pass
            yield "Não consegui responder agora. Tente novamente em alguns segundos."
        except Exception as error:
            if self._generation_cancelled.is_set():
                return
            print(f"[VegaAI] Erro no OpenAI: {error}")
            yield "Não consegui responder agora. Tente novamente em alguns segundos."

    def _format_prompt(self, messages, system_prompt=None):
        parts = [f"System: {system_prompt or self.system_prompt}\n"]
        for message in messages:
            if isinstance(message, dict) and message.get("content") is not None:
                role = message.get("role", "user").capitalize()
                content = self._coerce_text(message["content"])
                parts.append(f"{role}: {content}\n")
        return "".join(parts)

    def chat(self, messages, system_prompt=None, num_predict=100, include_screen=False, include_image=None):
        """
        Mantém compatibilidade com chamadas tradicionais.
        """

        answer = self._clean_response(
            "".join(str(part) for part in self.chat_stream(
                messages,
                system_prompt=system_prompt,
                num_predict=num_predict,
                include_screen=include_screen,
                include_image=include_image,
            ))
        ).strip()
        return answer or "Não consegui gerar uma resposta com o Gemini."


