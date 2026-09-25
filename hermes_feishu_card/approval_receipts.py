"""Display-only evidence for a separately delivered, complete approval receipt."""
from hashlib import sha256
import json


def approval_receipt_fingerprint(session, interaction):
    if (interaction is None or interaction.kind != 'approval'
            or interaction.status != 'completed' or not interaction.feishu_message_id):
        return ''
    data = [session.chat_id, session.conversation_id, session.message_id, session.route_profile_id,
            interaction.interaction_id, interaction.feishu_message_id, interaction.kind,
            interaction.status, interaction.prompt, interaction.description,
            [(option.label, option.value) for option in interaction.options],
            interaction.choice, interaction.choice_label, interaction.user_name]
    return sha256(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def has_confirmed_approval_receipt(session):
    interaction = session.active_interaction
    expected = approval_receipt_fingerprint(session, interaction)
    return bool(expected and interaction.receipt_fingerprint == expected)
