import json
import os
import time
import pytest
from hermes_feishu_card.session_store import SessionStore
from hermes_feishu_card.session import CardSession


def save(store):
    s=CardSession('conversation','turn','chat');s.answer_text='private body'
    store.save('turn',s,'om_fixture',None,'',{},'fixture_client')


def test_private_checkpoint_and_corruption(tmp_path):
    store=SessionStore(tmp_path);save(store)
    p=next(store.root.glob('*.json'))
    if os.name!='nt':assert p.stat().st_mode & 0o777 == 0o600
    assert store.load()[0]['session'].answer_text=='private body'
    data=json.loads(p.read_text());data['record']['message_id']='foreign'
    p.write_text(json.dumps(data))
    assert store.load()==[]


def test_expired_records_are_removed(tmp_path):
    store=SessionStore(tmp_path);save(store)
    p=next(store.root.glob('*.json'));os.utime(p,(time.time()-90000,)*2)
    assert store.load()==[] and not p.exists()


@pytest.mark.skipif(os.name=='nt',reason='POSIX symlink safety')
def test_checkpoint_refuses_symlink(tmp_path):
    store=SessionStore(tmp_path);save(store);p=next(store.root.glob('*.json'))
    p.unlink();target=tmp_path/'outside';target.write_text('unchanged');p.symlink_to(target)
    with pytest.raises(OSError):save(store)
    assert target.read_text()=='unchanged'


def test_checkpoint_does_not_store_approval_credentials(tmp_path):
    from hermes_feishu_card.session import InteractionState
    store=SessionStore(tmp_path);s=CardSession('conversation','turn','chat')
    s.active_interaction=InteractionState('fixture','approval','Approve?',callback_token='SECRET_FIXTURE_TOKEN')
    store.save('turn',s,'om_fixture',None,'',{},'fixture_client')
    assert 'SECRET_FIXTURE_TOKEN' not in next(store.root.glob('*.json')).read_text()
    r=store.load()[0]['session'];assert r.active_interaction is None and r.status=='failed'
