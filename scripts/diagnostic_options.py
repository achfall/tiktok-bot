"""Pilihan E2E sekali jalan, dipakai API, worker dan CLI tanpa mengubah akun."""
from datetime import datetime, timedelta
import json
from schedule_store import WIB, MIN_LEAD_MINUTES, now_wib


MAX_SCHEDULE_DAYS = 10
SCHEDULE_STEP_MINUTES = 5


def validate_options(options, now=None):
    if not isinstance(options, dict) or set(options) - {'mode', 'music', 'scheduled_at'}:
        raise ValueError('Pilihan tes tidak valid.')
    mode = options.get('mode', 'manual_review')
    music = options.get('music', 'account')
    if mode not in ('manual_review', 'auto', 'schedule'):
        raise ValueError('Mode tes tidak valid.')
    if music not in ('account', 'on', 'off'):
        raise ValueError('Pilihan musik tes tidak valid.')
    result = {'mode': mode, 'music': music}
    target = options.get('scheduled_at')
    if mode == 'schedule':
        try:
            target = datetime.fromisoformat(target)
        except (TypeError, ValueError):
            raise ValueError('Pilih tanggal dan jam tayang WIB.') from None
        if target.tzinfo is None:
            target = target.replace(tzinfo=WIB)
        target = target.astimezone(WIB)
        now = now or now_wib()
        if target.second or target.microsecond or target.minute % SCHEDULE_STEP_MINUTES:
            raise ValueError('Jam Schedule harus kelipatan 5 menit.')
        if not now + timedelta(minutes=MIN_LEAD_MINUTES) <= target <= now + timedelta(days=MAX_SCHEDULE_DAYS):
            raise ValueError(f'Waktu tes Schedule harus {MIN_LEAD_MINUTES} menit sampai 10 hari dari sekarang.')
        result['scheduled_at'] = target.isoformat()
    elif target:
        raise ValueError('Waktu tayang hanya untuk tes Schedule.')
    return result


def cli_options(argv):
    if '--test-options' not in argv:
        return None
    if '--publish' not in argv or '--retest-content' not in argv or '--test-draft' in argv:
        raise ValueError('--test-options memerlukan --publish --retest-content dan tidak boleh digabung --test-draft.')
    try:
        options = json.loads(argv[argv.index('--test-options') + 1])
    except (IndexError, ValueError):
        raise ValueError('--test-options memerlukan JSON pilihan tes.') from None
    return validate_options(options)
