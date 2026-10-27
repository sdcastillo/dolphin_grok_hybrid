"""Small SOAP front for Echo, Transmit, SendMessage, GetHealth, and GetHeadlines."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

NS = "http://milleniumfalcon.local/soap"


class SoapFault(Exception):
    def __init__(self, message: str, code: str = "Client"):
        super().__init__(message)
        self.message = message
        self.code = code


def build_fault(message: str, code: str = "Server") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body><soap:Fault>"
        f"<faultcode>soap:{escape(code)}</faultcode>"
        f"<faultstring>{escape(message)}</faultstring>"
        "</soap:Fault></soap:Body></soap:Envelope>"
    )


def generate_wsdl(endpoint: str) -> str:
    ops = ("Echo", "Transmit", "SendMessage", "GetHealth", "GetHeadlines")
    port_ops = "\n".join(
        f'    <operation name="{name}"><input message="tns:{name}Request"/>'
        f'<output message="tns:{name}Response"/></operation>'
        for name in ops
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<definitions name="MilleniumFalcon" targetNamespace="{NS}" xmlns:tns="{NS}"
 xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/" xmlns="http://schemas.xmlsoap.org/wsdl/">
  <portType name="DolphinPort">
{port_ops}
  </portType>
  <service name="MilleniumFalcon">
    <port name="Dolphin" binding="tns:DolphinBinding">
      <soap:address location="{escape(endpoint)}"/>
    </port>
  </service>
</definitions>
"""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _operation(body: bytes) -> tuple[str, ET.Element]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SoapFault(f"Invalid XML: {exc}", "Client") from exc
    soap_body = None
    for node in root.iter():
        if _local(node.tag) == "Body":
            soap_body = node
            break
    if soap_body is None or len(list(soap_body)) == 0:
        raise SoapFault("SOAP Body is empty", "Client")
    op = list(soap_body)[0]
    return _local(op.tag), op


def _text(op: ET.Element, *names: str) -> str:
    wanted = {n.lower() for n in names}
    for child in op.iter():
        if _local(child.tag).lower() in wanted and (child.text or "").strip():
            return child.text.strip()
    return (op.text or "").strip()


def _envelope(operation: str, payload: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
        f' xmlns:tns="{NS}">'
        f"<soap:Body><tns:{operation}Response>{payload}</tns:{operation}Response>"
        "</soap:Body></soap:Envelope>"
    )


async def handle_soap_request(
    body: bytes,
    *,
    ollama_base_url: str,
    ollama_model: str,
    build_ollama_payload,
    get_stored_trends,
    cloak_status,
    tts_status,
) -> str:
    import httpx

    name, op = _operation(body)
    if name == "Echo":
        text = _text(op, "text", "message") or "ok"
        return _envelope(name, f"<text>{escape(text)}</text>")
    if name == "GetHealth":
        cloak = cloak_status() or {}
        voice = tts_status() or {}
        return _envelope(
            name,
            "<mode>{}</mode><tts>{}</tts><model>{}</model>".format(
                escape(str(cloak.get("mode", ""))),
                escape("ready" if voice.get("ready") else "not-ready"),
                escape(ollama_model),
            ),
        )
    if name == "GetHeadlines":
        trends = get_stored_trends(limit=8) or []
        items = "".join(
            f"<headline>{escape(str(row.get('topic') or ''))}</headline>"
            for row in trends
            if isinstance(row, dict)
        )
        return _envelope(name, items or "<headline></headline>")
    if name in {"Transmit", "SendMessage"}:
        message = _text(op, "message", "text", "prompt")
        if not message:
            raise SoapFault("message is required", "Client")
        payload = build_ollama_payload(message, "PG")
        async with httpx.AsyncClient(timeout=120.0) as client:
            res = await client.post(f"{ollama_base_url.rstrip('/')}/api/chat", json=payload)
            res.raise_for_status()
            reply = (res.json().get("message") or {}).get("content") or ""
        return _envelope(name, f"<reply>{escape(reply)}</reply>")
    raise SoapFault(f"Unknown operation {name}", "Client")
