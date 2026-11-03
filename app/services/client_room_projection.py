"""Owner-facing room presentation over durable Coordinator records.

No provider calls, admissions or state changes belong in this projection.
Private consultations are deliberately excluded from the public transcript.
"""


def event_view(event):
    return {"schema_version": 1, "room_id": event.room_id, "sequence": event.sequence,
        "run_id": event.run_id, "step_id": event.step_id, "speaker": event.speaker,
        "kind": event.kind, "payload": event.payload,
        "created_at": event.created_at.isoformat() + "Z"}


def history(rows, events, steps):
    private = {step.id for step in steps if step.mode == 'consult'}
    rows = [row for row in rows if row.get('coordinationStepId') not in private]
    by_id = {row['id']: row for row in rows}
    sequenced, partials = {}, {}
    delivered = {event.step_id for event in events if event.kind in {'message', 'consult_result'}}
    started = {event.step_id: event.sequence for event in events if event.kind == 'speaker_started'}
    for event in events:
        if event.step_id in private:
            continue
        data = event.payload
        if event.kind == 'membership':
            sequenced[f'notice:{event.sequence}'] = {'id': f'room:{event.room_id}:{event.sequence}',
                'role': 'assistant', 'content': '', 'tools': [], 'isStreaming': False, 'isError': False,
                'roomSequence': event.sequence, 'roomNotice': data, 'createdAt': event.created_at.isoformat() + 'Z'}
        elif event.kind in {'user_message', 'automation_input', 'message'}:
            row = by_id.get(str(data.get('message_id')))
            if row:
                row = {**row, 'roomSequence': event.sequence, 'roomOrder': started.get(event.step_id, event.sequence),
                    'createdAt': event.created_at.isoformat() + 'Z'}
                # A regenerate references an existing owner original, not a second bubble.
                sequenced[row['id']] = row
        elif event.step_id and event.step_id not in delivered and event.kind in {
                'speaker_started', 'delta', 'tool', 'tool_result', 'file', 'contribution_stopped'}:
            row = partials.setdefault(event.step_id, {'id': 'coord:' + event.step_id,
                'coordinationStepId': event.step_id, 'speaker': event.speaker, 'role': 'assistant',
                'content': '', 'tools': [], 'files': [], 'isStreaming': False, 'isError': False,
                'roomSequence': event.sequence, 'createdAt': event.created_at.isoformat() + 'Z'})
            if event.kind == 'delta':
                row['content'] += data['text']
            elif event.kind == 'tool':
                row['tools'].append({**data['data'], 'afterChars': len(row['content'])})
            elif event.kind == 'tool_result':
                for tool in row['tools']:
                    if tool['id'] == data['data']['id']:
                        tool['result'] = data['data']['result']
            elif event.kind == 'file':
                row['files'].append(data['data'])
            elif event.kind == 'contribution_stopped':
                row['isError'], row['errorNote'] = True, data['reason']
    unbound = [row for row in rows if row['id'] not in sequenced]
    statuses = {step.id: step.status for step in steps}
    for step_id, row in partials.items():
        if statuses.get(step_id) not in {'running', 'queued'}:
            row['isError'] = True
            row.setdefault('errorNote', 'This contribution stopped before final delivery. Partial output is retained.')
    return unbound + sorted([*sequenced.values(), *partials.values()], key=lambda row: row.get('roomOrder', row['roomSequence']))
