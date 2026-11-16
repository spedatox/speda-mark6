"""Bounded watchdog response using the host's owner-approved protection policy."""
import asyncio

from app.core.context import AgentContext
from app.services.host_operations import host_operation


class SecurityResponder:
    async def __call__(self, facts):
        if facts.get('event') not in {'security_ingress_bps', 'security_ingress_pps', 'security_http_flood'}:
            return None
        identity = facts.get('incident_id', '')
        if len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
            return None
        context = AgentContext(user_id=1, session_id=0, request_id=identity,
            triggered_by='agent', trigger_payload={}, output_mode='silent', model='', system_prompt='',
            conversation_history=[], db=None, agent_id='orion', tool_call_id='watchdog-protection')
        reply = await host_operation({'action':'security_response', 'timeout':30}, context)
        operation_id = reply.get('operation_id')
        for _ in range(25):
            if reply.get('status') not in {'started', 'scheduled', 'running', 'accepted'} or not operation_id:
                break
            await asyncio.sleep(1)
            reply = await host_operation({'action':'operation_status', 'operation_id':operation_id}, context)
        if reply.get('status') != 'ok':
            return 'Automatic host protection was not confirmed (' + str(reply.get('status', 'unknown')) + '). ' + str(reply.get('reason', ''))[:300]
        reports = []
        for port in reply.get('protections', []):
            reports.append('UDP/' + str(port['port']) + ': ' + str(port['state'])
                + ('; firewall rule added and verified' if port.get('changed') and port['state'] == 'blocked' else ''))
        reports.append(str(reply.get('limitation', '')))
        return 'Automatic host response: ' + '; '.join(reports)
