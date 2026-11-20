"""Deterministic literal/regexp scan and exact-span replacement on a frozen script."""
import hashlib
import json
import re
import sys
from pathlib import Path


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan(text, rules, purpose=None):
    rules = [r for r in rules if not r.get("purposes") or purpose in r["purposes"]]
    findings = []
    for rule in rules:
        for match in re.finditer(rule["pattern"], text, flags=re.IGNORECASE):
            findings.append({"rule":rule["id"], "reason":rule["reason"],
                             "start":match.start(),"end":match.end(),"old":match.group(),
                             "line":text.count("\n",0,match.start())+1})
    return {"script_sha":sha(text), "purpose":purpose, "lexical_hits":findings, "rule_count":len(rules)}


def apply(text, patch):
    if patch["script_sha"] != sha(text):
        raise ValueError("脚本版本已改变，请重新审查")
    edits = sorted(patch["edits"], key=lambda e:e["start"], reverse=True)
    upper = len(text)
    if not edits:
        raise ValueError("修订为空")
    for edit in edits:
        a,b = edit["start"],edit["end"]
        if not 0 <= a < b <= upper or text[a:b] != edit["old"]:
            raise ValueError("修订范围重叠或原句已经改变")
        if not isinstance(edit["new"],str):
            raise ValueError("替换内容须为文本")
        text = text[:a] + edit["new"] + text[b:]
        upper = a
    return text


def main():
    cmd,source,argument = sys.argv[1:4]
    text = Path(source).read_bytes().decode("utf-8")
    args = json.loads(Path(argument).read_text(encoding="utf-8"))
    if cmd=="scan" and len(sys.argv) in (4,5):
        purpose = sys.argv[4] if len(sys.argv)==5 else None
        result=scan(text,args,purpose)
    elif cmd=="apply" and len(sys.argv)==5:
        new=apply(text,args)
        with Path(sys.argv[4]).open("xb") as f:
            f.write(new.encode("utf-8"))
        result={"new_script_sha":sha(new),"recheck_required":True}
    else:
        raise ValueError("用法 scan 脚本 规则.json [业务目的] | apply 脚本 修订.json 新脚本")
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    try: main()
    except (OSError,IndexError,KeyError,ValueError,re.error) as exc:
        sys.exit(str(exc))
