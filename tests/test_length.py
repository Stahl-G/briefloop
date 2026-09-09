"""Mechanical count, requirement defaults and the pre-publication length contract."""
import json
import sys
import pytest
from briefloop.length import count_brief,length_stats
from briefloop.models import Requirements,LENGTH_PRESETS
from briefloop.store import Store,dump
from briefloop.runtime import generation_prompt


def test_length_rule_and_cli_use_the_same_body_count(tmp_path,monkeypatch,capsys):
    markdown='## 中国 **solar PV2026** 增长 12% [@src_123]\n\n[公告](https://example.com/a) https://example.com/b\n'
    assert count_brief(markdown)==9
    assert count_brief('**Open**AI与2026年')==4
    assert count_brief('| 中文 | ABC |\n|---|---|\n| 值 | 12 |')==5
    assert length_stats(markdown)['over_limit'] is None
    assert length_stats(markdown,target_words=5,max_words=8)['over_by']==1
    from briefloop.cli import main
    path=tmp_path/'body.md';path.write_text(markdown)
    monkeypatch.setattr(sys,'argv',['briefloop','tool','--workspace',str(tmp_path/'workspace'),'count-brief','--file',str(path),'--max-words','8'])
    main()
    assert json.loads(capsys.readouterr().out)['count']==9


def test_presets_custom_bounds_and_generation_keep_citations_out_of_body(tmp_path):
    for extent,bounds in LENGTH_PRESETS.items():
        req=Requirements(title='test',objective='read',extent=extent)
        assert (req.target_words,req.max_words)==bounds
    req=Requirements(title='test',objective='read',target_words=1234,max_words=1600)
    assert (req.target_words,req.max_words)==(1234,1600)
    with pytest.raises(ValueError):Requirements(title='test',objective='read',target_words=100,max_words=99)
    store=Store(tmp_path/'workspace');source=store.add_source('local','正文')
    run=store.create_run(req.model_dump(),[source['id']]);original=run['requirements']
    folder=store.root/'jobs'/'prompt';folder.mkdir()
    prompt=generation_prompt(store,run,folder)
    assert '正文目标约 1234，上限 1600' in prompt
    assert 'count-brief --file' in prompt and '仅放进 draft.json.citations 元数据' in prompt
    assert '覆盖已经足够时收敛' in prompt
    assert store.one('runs',run['id'])['requirements']==original
    # Legacy requirements are projected on read, never rewritten in the database.
    legacy={'title':'old','objective':'read','extent':'compact','allow_web':False}
    with store.tx() as connection:connection.execute('UPDATE runs SET requirements=? WHERE id=?',(dump(legacy),run['id']))
    generation_prompt(store,store.one('runs',run['id']),folder)
    assert json.loads(store.one('runs',run['id'])['requirements'])==legacy
    assert json.loads((folder/'input.json').read_text())['requirements']['max_words']==1000
