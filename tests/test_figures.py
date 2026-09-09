import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from briefloop.figures import figure_ids, read_figure, register_figure
from briefloop.store import Store


def inputs(store):
    source=store.add_source('measurements.csv','period,value\nA,10\nB,15\n')
    run=store.create_run({'title':'Synthetic figure test','objective':'Compare values'},[source['id']])
    image=Image.new('RGB',(12,6),(20,110,70));buffer=io.BytesIO();exif=Image.Exif();exif[274]=6
    image.save(buffer,format='JPEG',exif=exif)
    path=store.root/'plot.jpeg';path.write_bytes(buffer.getvalue())
    return source,run,path


def test_figure_snapshots_are_independent_and_script_is_not_executed(tmp_path):
    store=Store(tmp_path);source,run,image=inputs(store)
    data=store.root/'data.csv';data.write_text('period,value\nA,10\nB,15\n')
    script=store.root/'draw.py';script.write_text("from pathlib import Path\nPath('SCRIPT_EXECUTED').write_text('bad')\n")
    original=image.read_bytes()
    registered=register_figure(store,run['id'],image,'趋势 [示意]',caption='示例数据',source_ids=[source['id'],source['id']],data_path=data,script_path=script)
    assert registered['source_ids']==[source['id']]
    assert registered['image_path'].startswith('figures/'+registered['figure_id']+'/')
    assert not (store.root/'SCRIPT_EXECUTED').exists()
    assert (store.root/registered['original_path']).read_bytes()==original
    with Image.open(store.root/registered['image_path']) as normalized:
        assert normalized.format=='PNG' and normalized.size==(6,12)
        assert normalized.getexif().get(274) is None
    data.write_text('changed');script.write_text('changed');image.write_bytes(b'changed')
    assert read_figure(store,registered['figure_id'],run['id'])==registered
    assert (store.root/registered['data_path']).read_text()=='period,value\nA,10\nB,15\n'
    assert figure_ids(registered['markdown']+'\n'+registered['markdown'])==[registered['figure_id']]
    assert figure_ids('![plain](https://example.test/a.png)')==[]


def test_registration_rejects_foreign_failed_reference_sources_and_outside_inputs(tmp_path):
    store=Store(tmp_path/'workspace');source,run,image=inputs(store)
    foreign=store.add_source('elsewhere','different run')
    with pytest.raises(ValueError,match='本轮'):register_figure(store,run['id'],image,'x',source_ids=[foreign['id']])
    failed=store.add_source('bad','',error='unreadable')
    failed_run=store.create_run({'title':'x','objective':'x'},[failed['id']])
    with pytest.raises(ValueError,match='可读取'):register_figure(store,failed_run['id'],image,'x',source_ids=[failed['id']])
    styled=store.create_run({'title':'x','objective':'x','reference_source_ids':[foreign['id']]},[source['id']])
    with pytest.raises(ValueError,match='风格参考'):register_figure(store,styled['id'],image,'x',source_ids=[foreign['id']])
    outside=tmp_path/'external.csv';outside.write_text('private')
    with pytest.raises(ValueError,match='工作区'):register_figure(store,run['id'],image,'x',source_ids=[source['id']],data_path=outside)
    link=store.root/'external-link.csv';link.symlink_to(outside)
    with pytest.raises(ValueError,match='工作区'):register_figure(store,run['id'],image,'x',source_ids=[source['id']],script_path=link)
    with pytest.raises(ValueError,match='无数值'):register_figure(store,run['id'],image,'x')
    structure=register_figure(store,run['id'],image,'流程',caption='纯结构示意，无数值。')
    assert structure['source_ids']==[]


def test_reader_verifies_ownership_hashes_and_snapshot_paths(tmp_path):
    store=Store(tmp_path/'workspace');source,run,image=inputs(store)
    figure=register_figure(store,run['id'],image,'x',source_ids=[source['id']])
    another=store.create_run({'title':'other','objective':'other'},[source['id']])
    with pytest.raises(ValueError,match='不属于'):read_figure(store,figure['figure_id'],another['id'])
    assert read_figure(store,figure['figure_id'])['run_id']==run['id']
    pixel=store.root/figure['image_path'];pixel.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='哈希'):read_figure(store,figure['figure_id'])
    second=register_figure(store,run['id'],image,'x',source_ids=[source['id']])
    folder=store.root/'figures'/second['figure_id'];outside=tmp_path/'outside.png';outside.write_bytes(b'outside')
    manifest=json.loads((folder/'manifest.json').read_text());manifest['image_path']=str(outside)
    payload=json.dumps(manifest).encode();(folder/'manifest.json').write_bytes(payload)
    (folder/'manifest.sha256').write_text(hashlib.sha256(payload).hexdigest())
    with pytest.raises(ValueError,match='快照路径'):read_figure(store,second['figure_id'])
    with pytest.raises(ValueError,match='ID'):read_figure(store,'../../outside')


def test_register_figure_cli_returns_insertable_marker(tmp_path,monkeypatch,capsys):
    from briefloop.cli import main
    store=Store(tmp_path);source,run,image=inputs(store)
    monkeypatch.setattr('sys.argv',['briefloop','tool','--workspace',str(store.root),'register-figure','--run',run['id'],'--image',str(image),'--title','Synthetic comparison','--caption','Source-linked figure','--source',source['id']])
    main();result=json.loads(capsys.readouterr().out)
    assert figure_ids(result['markdown'])==[result['figure_id']]
    assert read_figure(store,result['figure_id'])['source_ids']==[source['id']]
