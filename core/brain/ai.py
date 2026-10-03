import asyncio
import inspect
import io
import json
import os
import re
import threading
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

from config.ai import (
    TEMPERATURE,
    CONTEXT_SIZE,
    GEMINI_API_KEY,
    GEMINI_MODEL_NAME,
    GEMINI_FALLBACK_MODEL,
    GEMINI_LIVE_MODEL,
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
    normalize_gemini_live_model_name,
    normalize_gemini_model_name,
)


VEGA_RUNTIME_PROMPT = """/no_think
Você é VEGA, uma assistente pessoal brasileira criada por Endriw.
Fale sempre em português brasileiro. Seja natural, direta, inteligente,
descontraída e breve quando a pergunta for simples. Você tem personalidade:
pode brincar, provocar levemente e discordar com respeito, mas nunca fale como
atendente corporativa ou chatbot genérico.

Endriw é seu criador e usuário principal; trate-o com familiaridade, sem
submissão. Não invente fatos sobre ele ou sobre qualquer pessoa.

As memórias internas, quando fornecidas, são fatos sobre o usuário. Use-as só
quando relevantes, de forma natural, e não exponha o contexto bruto. Nunca diga
que não possui memória se houver memórias disponíveis.

Não exponha raciocínio interno, não escreva análise antes da resposta e responda
diretamente ao que foi perguntado.

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

""" + build_personality_context()


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
        self.system_prompt = VEGA_RUNTIME_PROMPT
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
                print(f"[VegaAI] Gemini indisponível em {model}: {error}")

        if not self._generation_cancelled.is_set() and not self._tool_calls_executed:
            raise RuntimeError("Nenhum modelo Gemini concluiu a solicitação com ferramentas.")

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
        return (
            "resource_exhausted" in text
            or "quota" in text
            or "exceeded your current quota" in text
            or "rate limit" in text
            or int(getattr(error, "status", 0) or 0) == 429
        )

    def _quota_exhausted_message(self):
        return (
            "A API do Gemini excedeu a cota de uso da chave atual. "
            "Troque a chave, recarregue a conta ou use outra chave com quota disponível."
        )

    def _capture_screen(self):
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
        client = self.gemini_client or self.client
        if client is None:
            yield "GEMINI_API_KEY não configurada."
            return

        try:
            prompt = self._format_prompt(messages, system_prompt)
            screen = self._capture_screen() if include_screen else None
            if include_image is not None:
                contents = [prompt, include_image]
            else:
                contents = [screen, prompt] if include_screen and screen is not None else prompt
            output_limit = min(max(num_predict, 128), MAX_OUTPUT_TOKENS)
            models = [model for model in (self.gemini_model, self.gemini_fallback_model) if model]
            quota_exhausted = False

            for model_index, model in enumerate(models):
                stream = None
                emitted = False
                try:
                    config = {
                        "temperature": TEMPERATURE,
                        "max_output_tokens": output_limit,
                    }
                    if model == self.gemini_fallback_model:
                        config["thinking_config"] = types.ThinkingConfig(
                            thinking_budget=0
                        )

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
                        print(
                            f"[VegaAI] Cota esgotada em {model}; "
                            "tentando o próximo modelo."
                        )
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
            yield from self._stream_with_fallback(providers)
            return

        if self.backend == "openrouter":
            providers = [openrouter]
            if OPENAI_API_KEY or self.gemini_client is not None:
                providers.append(openai)
                if self.gemini_client is not None:
                    providers.append(gemini)
            yield from self._stream_with_fallback(providers)
            return

        if self.backend == "openai":
            providers = [openai]
            if self.gemini_client is not None:
                providers.append(gemini)
            yield from self._stream_with_fallback(providers)
            return

        yield from self._stream_with_fallback([gemini])

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

    def _live_model_candidates(self):
        candidates = []
        for value in (
            GEMINI_LIVE_MODEL,
            os.getenv("GEMINI_LIVE_MODEL", ""),
            os.getenv("GEMINI_MODEL_NAME", ""),
        ):
            normalized = normalize_gemini_live_model_name(value)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

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


class VegaLiveSession:
    """Sessão multimodal Gemini Live integrada à arquitetura atual da VEGA.

    Usa o Gemini para percepção em tempo real (microfone/câmera/tela).
    """

    def __init__(self, video_mode="camera", speaker=None, camera_index=1, microphone_index=1):
        self.video_mode = video_mode
        self.camera_index = camera_index
        self.microphone_index = microphone_index
        self.client = None
        self.session = None
        self.audio_stream = None
        self.audio_in_queue = None
        self.out_queue = None
        self._stop_event = None
        self.speaker = speaker
        self._api_key = GEMINI_API_KEY

        if self._api_key:
            try:
                self.client = genai.Client(
                    http_options={"api_version": "v1beta"},
                    api_key=self._api_key,
                )
            except Exception as error:
                print(f"[VegaLiveSession] Falha ao iniciar Gemini: {error}")
                self.client = None

        self.config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=0,
                sliding_window=types.SlidingWindow(target_tokens=0),
            ),
            system_instruction=(
                "Você é a VEGA. Responda sempre em português brasileiro, "
                "de forma direta e natural, em frases curtas. "
                "Responda diretamente ao que o usuário falou; não narre ou descreva a fala do usuário como se estivesse analisando o discurso. "
                "Se o usuário te cumprimentar, cumprimentar de volta; se fizer pergunta, responda a pergunta; "
                "se o usuário pedir algo visual, descreva imagens apenas quando for relevante. "
                "Nunca invente o que está vendo: se não receber uma imagem válida, diga claramente que não conseguiu ver. "
                "Não use explicações longas sobre o que o usuário disse; responda em 1 a 3 frases curtas e naturais."
            ),
        )

    def _get_frame(self, cap):
        if cap is None:
            return None

        ret, frame = cap.read()
        if not ret or frame is None:
            return None

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(frame_rgb)
        img.thumbnail([640, 480])

        image_io = io.BytesIO()
        img.save(image_io, format="jpeg", quality=65, optimize=True)
        image_io.seek(0)
        image_bytes = image_io.read()

        return {
            "mime_type": "image/jpeg",
            "data": image_bytes,
        }

    async def _get_frames_loop(self):
        cap = await asyncio.to_thread(
            cv2.VideoCapture,
            self.camera_index,
            cv2.CAP_DSHOW,
        )
        try:
            if not cap.isOpened():
                raise RuntimeError("A câmera do Gemini Live não pôde ser aberta.")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            first_frame = await asyncio.to_thread(self._get_frame, cap)
            if first_frame is None:
                raise RuntimeError("A câmera do Gemini Live abriu, mas não entregou imagem.")
            if self.out_queue is not None:
                await self.out_queue.put(first_frame)
            while self._stop_event is None or not self._stop_event.is_set():
                frame = await asyncio.to_thread(self._get_frame, cap)
                if frame is None:
                    break
                await asyncio.sleep(0.75)
                if self.out_queue is not None:
                    await self.out_queue.put(frame)
        except Exception as error:
            print(f"[VegaLiveSession] Câmera encerrada: {error}")
            if self._stop_event is not None:
                self._stop_event.set()
        finally:
            cap.release()

    def _get_screen_frame(self):
        try:
            import mss  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "Para usar a tela do Gemini Live, instale 'mss' em ambiente do projeto."
            ) from exc

        sct = mss.mss()
        monitor = sct.monitors[0]
        image = sct.grab(monitor)
        image_bytes = mss.tools.to_png(image.rgb, image.size)
        img = PIL.Image.open(io.BytesIO(image_bytes))

        output = io.BytesIO()
        img.save(output, format="jpeg")
        output.seek(0)

        return {
            "mime_type": "image/jpeg",
            "data": output.read(),
        }

    async def _get_screen_loop(self):
        while self._stop_event is None or not self._stop_event.is_set():
            await asyncio.sleep(1.0)
            frame = await asyncio.to_thread(self._get_screen_frame)
            if self.out_queue is not None:
                await self.out_queue.put(frame)

    async def _send_realtime(self):
        while self._stop_event is None or not self._stop_event.is_set():
            if self.out_queue is None:
                await asyncio.sleep(0.1)
                continue
            try:
                message = await asyncio.wait_for(
                    self.out_queue.get(),
                    timeout=0.25,
                )
            except asyncio.TimeoutError:
                continue
            if self.session is not None:
                mime_type = message.get("mime_type", "")
                try:
                    blob = types.Blob(
                        data=message.get("data", b""),
                        mime_type=mime_type,
                    )
                    if mime_type.startswith("audio/"):
                        await self.session.send_realtime_input(audio=blob)
                    elif mime_type.startswith("image/"):
                        await self.session.send_realtime_input(media=blob)
                    elif mime_type.startswith("video/"):
                        await self.session.send_realtime_input(video=blob)
                except Exception as error:
                    if self._stop_event is not None:
                        self._stop_event.set()
                    print(f"[VegaLiveSession] Envio Live encerrado: {error}")

    def _audio_callback(self, indata, frames, time_info, status):
        if self.out_queue is None or (
            self._stop_event is not None and self._stop_event.is_set()
        ):
            return

        try:
            self.out_queue.put_nowait({
                "data": indata.tobytes(),
                "mime_type": "audio/pcm;rate=16000",
            })
        except Exception:
            pass

    async def _listen_audio(self):
        if not self.client or not self._api_key:
            return

        try:
            self.audio_stream = sd.InputStream(
                samplerate=16000,
                channels=1,
                dtype="int16",
                device=self.microphone_index,
                callback=self._audio_callback,
            )
            self.audio_stream.start()

            while self.audio_stream.active and (
                self._stop_event is None or not self._stop_event.is_set()
            ):
                await asyncio.sleep(0.05)
        except Exception as error:
            print(f"[VegaLiveSession] Microfone Live indisponível: {error}")
            if self._stop_event is not None:
                self._stop_event.set()
        finally:
            if self.audio_stream is not None:
                self.audio_stream.stop()
                self.audio_stream.close()

    async def _receive_audio(self):
        try:
            while self._stop_event is None or not self._stop_event.is_set():
                if self.session is None:
                    await asyncio.sleep(0.1)
                    continue
                turn = self.session.receive()
                async for response in turn:
                    server_content = getattr(response, "server_content", None)
                    model_turn = getattr(server_content, "model_turn", None)
                    parts = getattr(model_turn, "parts", None) or []
                    for part in parts:
                        inline_data = getattr(part, "inline_data", None)
                        data = getattr(inline_data, "data", None)
                        if data and self.audio_in_queue is not None:
                            self.audio_in_queue.put_nowait(data)
        except Exception as error:
            print(f"[VegaLiveSession] Recepção encerrada: {error}")
            if self._stop_event is not None:
                self._stop_event.set()

    async def _play_audio(self):
        if self.audio_in_queue is None:
            return

        output_stream = None
        try:
            output_stream = sd.RawOutputStream(
                samplerate=24000,
                channels=1,
                dtype="int16",
                latency="low",
            )
            output_stream.start()

            while self._stop_event is None or not self._stop_event.is_set():
                try:
                    bytestream = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.25,
                    )
                except asyncio.TimeoutError:
                    continue

                if bytestream:
                    await asyncio.to_thread(output_stream.write, bytestream)
        except Exception as error:
            print(f"[VegaLiveSession] Saída de áudio indisponível: {error}")
            if self._stop_event is not None:
                self._stop_event.set()
        finally:
            if output_stream is not None:
                output_stream.stop()
                output_stream.close()

    def _live_model_candidates(self):
        candidates = []
        for value in (
            GEMINI_LIVE_MODEL,
            os.getenv("GEMINI_LIVE_MODEL", ""),
            os.getenv("GEMINI_MODEL_NAME", ""),
        ):
            normalized = normalize_gemini_live_model_name(value)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    async def start(self):
        if not self.client or not self._api_key:
            raise RuntimeError(
                "GEMINI_API_KEY ausente. Configure a chave antes de ativar o live mode da VEGA."
            )

        live_models = self._live_model_candidates()
        if not live_models:
            raise RuntimeError(
                "O Gemini Live está desativado neste ambiente. Use o modelo de texto do Gemini e mantenha o modo live desligado."
            )

        last_error = None
        for model_name in live_models:
            try:
                async with (
                    self.client.aio.live.connect(
                        model=model_name,
                        config=self.config,
                    ) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session = session
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue = asyncio.Queue(maxsize=5)
                    self._stop_event = asyncio.Event()

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    if self.video_mode == "camera":
                        tg.create_task(self._get_frames_loop())
                    elif self.video_mode == "screen":
                        tg.create_task(self._get_screen_loop())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

                    while not self._stop_event.is_set():
                        await asyncio.sleep(1)
                return
            except Exception as error:
                last_error = error
                print(f"[VegaLiveSession] Modelo Gemini Live falhou ({model_name}): {error}")

        raise RuntimeError(f"Nenhum modelo Gemini Live funcionou. Último erro: {last_error}")

    def run(self):
        try:
            asyncio.run(self.start())
        except Exception as error:
            print(f"[VegaLiveSession] Sessão encerrada: {error}")
            traceback.print_exception(type(error), error, error.__traceback__)
