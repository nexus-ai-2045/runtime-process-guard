from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

SCHEMA = 'runtime-process-guard/hook-observation-v1'
LIMIT = 128


def _digest(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    return hashlib.sha256(value.encode()).hexdigest()


def _diagnostic_self(response) -> dict | None:
    if not isinstance(response, dict) or response.get('isError') is True:
        return None
    content = response.get('content')
    if not isinstance(content, list):
        return None
    for part in content[:8]:
        text = part.get('text') if isinstance(part, dict) else None
        if not isinstance(text, str) or len(text) > 16384:
            continue
        try:
            report = json.loads(text)
            if (not isinstance(report, dict)
                    or report.get('schema') != 'runtime-process-guard/stdio-probe-v1'
                    or report.get('observation_scope') != 'self-process-metadata-only'):
                continue
            identity = report.get('self')
            if (not isinstance(identity, dict) or identity.get('status') != 'observed'
                    or type(identity.get('pid')) is not int or identity['pid'] <= 0
                    or not isinstance(identity.get('created_at'), str)):
                continue
            created = datetime.fromisoformat(identity['created_at'].replace('Z', '+00:00'))
            if created.utcoffset() is None or created.timestamp() <= 0:
                continue
            return {'pid': identity['pid'], 'created_at': created.astimezone(timezone.utc).isoformat(),
                    'status': 'self-reported-only'}
        except (ValueError, TypeError, OverflowError):
            continue
    return None


def normalize_event(event: dict, server: str) -> dict | None:
    if not isinstance(event, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', server):
        raise ValueError('invalid-probe-input')
    name = event.get('tool_name')
    match = re.fullmatch(r'(?:functions\.)?mcp__([A-Za-z0-9_-]+)__([A-Za-z0-9_-]+)', name) if isinstance(name, str) else None
    if not match or match[1] != server:
        return None
    raw_phase = event.get('hook_event_name')
    phase = {'PreToolUse': 'start', 'PostToolUse': 'response'}.get(raw_phase, 'unknown') if isinstance(raw_phase, str) else 'unknown'
    row = {'schema': SCHEMA, 'phase': phase,
            'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'session': _digest(event.get('session_id')), 'turn': _digest(event.get('turn_id')),
            'call': _digest(event.get('tool_use_id')), 'tool': _digest(name.removeprefix('functions.')),
            'transport_metadata_present': any(k in event for k in ('connection_id', 'transport_id', 'server_pid')),
            'observed_at': datetime.now(timezone.utc).isoformat()}
    if server == 'rpg_stdio_probe' and phase == 'response':
        identity = _diagnostic_self(event.get('tool_response'))
        if identity is not None:
            row['reported_process'] = identity
    return row


def analyze_events(rows: list[dict]) -> dict:
    calls = {}
    unknown = not rows
    duplicates = 0
    sources = set()
    for row in rows:
        if (not isinstance(row, dict) or row.get('schema') != SCHEMA
                or row.get('phase') not in ('start', 'response')
                or any(not isinstance(row.get(k), str) or not re.fullmatch('[0-9a-f]{64}', row[k])
                       for k in ('session', 'turn', 'call', 'tool', 'source_sha256'))):
            unknown = True
            continue
        sources.add(row['source_sha256'])
        key = tuple(row[k] for k in ('session', 'turn', 'call'))
        previous = calls.get(key)
        if previous is None:
            calls[key] = (row['tool'], {row['phase']: _digest(json.dumps(row.get('reported_process'), sort_keys=True))})
            if row['phase'] != 'start':
                unknown = True
        elif previous[0] != row['tool']:
            unknown = True
        elif row['phase'] in previous[1]:
            if previous[1][row['phase']] != _digest(json.dumps(row.get('reported_process'), sort_keys=True)):
                unknown = True
            else:
                duplicates += 1
        elif 'start' in previous[1] and row['phase'] == 'response':
            previous[1]['response'] = _digest(json.dumps(row.get('reported_process'), sort_keys=True))
        else:
            unknown = True
    in_flight = sum('start' in phases and 'response' not in phases for _, phases in calls.values())
    if len(sources) != 1:
        unknown = True
    return {'schema': 'runtime-process-guard/hook-probe-report-v1',
            'call_state': 'unknown' if unknown else 'in_flight_observed' if in_flight else 'no_in_flight_observed',
            'call_count': len(calls), 'in_flight_observed': in_flight, 'duplicate_count': duplicates,
            'coverage_status': 'unverified', 'binding_status': 'unverified_binding',
            'background_work_status': 'unverified', 'protection_required': True,
            'stop_authorized': False, 'process_control_performed': False}


def _directory(path: Path, *, create: bool = True) -> Path:
    if os.name != 'posix':
        raise ValueError('unsupported-private-report-platform')
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('unsafe-report-directory')
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.stat().st_mode & 0o077:
        raise ValueError('unsafe-report-directory')
    return path


def record_event(event: dict, server: str, directory: Path) -> Path | None:
    row = normalize_event(event, server)
    if row is None:
        return None
    directory = _directory(directory)
    for i in range(LIMIT):
        path = directory / f'event-{i:04d}.json'
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, 'w') as output:
            json.dump(row, output, sort_keys=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        return path
    raise ValueError('probe-capacity-exhausted')


class ProbeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('invalid-probe-options')


def main() -> int:
    parser = ProbeParser()
    parser.add_argument('--server', required=True)
    parser.add_argument('--report-dir', required=True, type=Path)
    parser.add_argument('--session-id', action='append')
    parser.add_argument('--summarize', action='store_true')
    try:
        args = parser.parse_args()
    except ValueError:
        print('hook-probe: invalid observer options; original tool remains unblocked', file=sys.stderr)
        print('{}')
        return 0
    try:
        if args.summarize:
            directory = _directory(args.report_dir, create=False)
            rows = []
            for path in sorted(directory.glob('event-*.json'))[:LIMIT + 1]:
                try:
                    if path.is_symlink() or path.stat().st_size > 8192:
                        raise ValueError('unsafe-report')
                    rows.append(json.loads(path.read_text()))
                except (OSError, ValueError):
                    rows.append({})
            if len(rows) >= LIMIT:
                rows.append({})
            print(json.dumps(analyze_events(rows), sort_keys=True))
        else:
            raw = sys.stdin.buffer.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('hook-input-too-large')
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError('invalid-hook-event')
            if args.session_id is None or event.get('session_id') in args.session_id:
                record_event(event, args.server, args.report_dir)
            print('{}')
        return 0
    except (ValueError, OSError, TypeError):
        if args.summarize:
            print(json.dumps(analyze_events([]), sort_keys=True))
            return 40
        print('hook-probe: observation unavailable; protection remains required', file=sys.stderr)
        print('{}')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
