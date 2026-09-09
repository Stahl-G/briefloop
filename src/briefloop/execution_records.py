"""Public task tool records for review, excluding credentials and reasoning."""
import hashlib
import json
import re
from .store import dump

_SECRET_KEY=re.compile(r'(?:api.?key|password|authorization|cookie|secret|private.?key|access.?token|refresh.?token)',re.I)
_SECRET_TEXT=re.compile(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+|\bsk-[A-Za-z0-9_-]{12,}')
_SECRET_ASSIGNMENT=re.compile(r'''(?ix)(["']?\b(?:[a-z0-9]+_)*(?:api_?key|password|access_?token|refresh_?token|client_?secret)["']?\s*[:=]\s*)(?:\[credential[ ]omitted\]|"[^"\n]*"|'[^'\n]*'|[^\s,;&}\]]+)''')
# Header values can contain spaces, semicolons and quoted cookie values. Mask
# the whole physical header line, not just a token or a particular auth scheme.
# This also covers curl -H/--header strings and verbose HTTP output. Removing a
# trailing command argument on that same line is preferable to retaining auth.
_SECRET_HEADER=re.compile(r'''(?im)(?<![\w-])((?:proxy[-_])?authorization|(?:set[-_])?cookie)(?:\\*["'])?[ \t]*:[ \t]*[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*''')
_SECRET_ENV=re.compile(r'''(?ix)(["']?\b(?:[a-z0-9]+_)*(?:authorization|cookie)["']?\s*=\s*)(?:\[credential[ ]omitted\]|"[^"\n]*"|'[^'\n]*'|[^\s,;&}\]]+)''')
_PRIVATE_KEY=re.compile(r'-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----.*?-----END (?:[A-Z ]+)?PRIVATE KEY-----',re.S)


def sanitize(value):
    if isinstance(value,dict):return {k:('[credential omitted]' if _SECRET_KEY.search(k) else sanitize(v)) for k,v in value.items()}
    if isinstance(value,list):return [sanitize(v) for v in value]
    if isinstance(value,str):
        value=_PRIVATE_KEY.sub('[credential omitted]',value)
        value=_SECRET_HEADER.sub(lambda match:match.group(1)+': [credential omitted]',value)
        value=_SECRET_ENV.sub(lambda match:match.group(1)+'[credential omitted]',value)
        value=_SECRET_ASSIGNMENT.sub(lambda match:match.group(1)+'[credential omitted]',value)
        return _SECRET_TEXT.sub('[credential omitted]',value)
    return value


def journal_tool(chat,sid,turn_id,identity,tool,inputs,output,*,status,exit_code=None,native_session=None,native_message_id=None,native_created_at=None):
    # This function consumes host event fields, never an author's claimed log.
    original={'tool_id':identity,'tool':tool,'input':inputs,'output':output,'status':status,'exit_code':exit_code,'native_session':native_session}
    if native_message_id is not None:original['native_message_id']=native_message_id
    if native_created_at is not None:original['native_created_at']=native_created_at
    value=sanitize(original);value['redacted']=value!=original
    encoded=dump(value)
    value['captured_hash']=hashlib.sha256(encoded.encode()).hexdigest()
    if len(encoded)>200000:
        value['output']=str(value['output'])[:180000];value['truncated']=True
    value['record_hash']=hashlib.sha256(dump(value).encode()).hexdigest()
    chat.event(sid,'tool/record',{'turnId':turn_id,'record':value})
