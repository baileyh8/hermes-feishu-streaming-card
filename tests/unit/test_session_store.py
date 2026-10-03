import json
import hashlib
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


def test_task_presentation_state_is_ephemeral_and_does_not_change_checkpoint_schema(tmp_path):
    store = SessionStore(tmp_path)
    value = CardSession('conversation', 'turn', 'chat')
    value.presentation_state = 'reconnecting'
    store.save('turn', value, 'om_fixture', None, '', {}, 'fixture_client')
    payload = json.loads(next(store.root.glob('*.json')).read_text())
    assert 'presentation_state' not in payload['record']['session']
    assert store.load()[0]['session'].presentation_state == ''


def test_pre_task_layout_checkpoint_still_loads_with_full_answer(tmp_path):
    store = SessionStore(tmp_path)
    save(store)
    path = next(store.root.glob('*.json'))
    payload = json.loads(path.read_text())
    # Pre-task-layout checkpoints have no presentation_state field. Retain
    # the real v1 envelope/digest so this tests schema compatibility, not corruption.
    payload['record']['session'].pop('presentation_state', None)
    encoded = json.dumps(payload['record'], ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
    payload['digest'] = hashlib.sha256(encoded).hexdigest()
    path.write_text(json.dumps(payload))
    records = store.load()
    assert len(records) == 1
    assert records[0]['session'].answer_text == 'private body'
    assert records[0]['session'].presentation_state == ''


def auxiliary():
    from hermes_feishu_card.legacy_owner import static_legacy_receipt
    return dict(message_id='om_auxiliary', dialect='legacy', template='red', card=static_legacy_receipt({
        'elements': [{'tag': 'markdown', 'content': 'KEEP_SCOPE 已失效'}]}))


@pytest.mark.parametrize('damage', ['controls', 'callback_field', 'id', 'owner', 'dialect', 'oversize', 'template'])
def test_auxiliary_checkpoint_refuses_unsafe_display_record(tmp_path, damage):
    store = SessionStore(tmp_path)
    value = auxiliary()
    if damage == 'controls':
        value['card']['elements'].append({'tag': 'action', 'actions': [{'tag': 'button'}]})
    elif damage == 'callback_field':
        value['callback_token'] = 'SECRET_FIXTURE_TOKEN'
    elif damage == 'id':
        value['message_id'] = 'x' * 257
    elif damage == 'owner':
        value['message_id'] = 'om_owner'
    elif damage == 'dialect':
        value['dialect'] = 'unknown'
    elif damage == 'template':
        value['template'] = 'unknown'
    else:
        value['card']['elements'][0]['content'] = 'x' * 28000
    with pytest.raises(ValueError):
        store.save('turn', CardSession('conversation', 'turn', 'chat'), 'om_owner', None, '', {},
                   'fixture_client', auxiliary_receipt=value)
    assert store.load() == []


def test_auxiliary_payload_is_bounded_and_validated_again_when_loading(tmp_path):
    store = SessionStore(tmp_path)
    store.save('turn', CardSession('conversation', 'turn', 'chat'), 'om_owner', None, '', {},
               'fixture_client', auxiliary_receipt=auxiliary())
    record = store.load()[0]
    assert record['auxiliary_receipt']['message_id'] == 'om_auxiliary'
    assert record['session'].active_interaction is None
    path = next(store.root.glob('*.json'))
    envelope = json.loads(path.read_text())
    envelope['record']['auxiliary_receipt']['card']['elements'].append(
        {'tag': 'button', 'value': {'token': 'SECRET_FIXTURE_TOKEN'}})
    envelope['digest'] = hashlib.sha256(json.dumps(envelope['record'], ensure_ascii=False,
        allow_nan=False, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(envelope))
    assert store.load() == []
