#!/usr/bin/env python3
"""Lihat klaim tertahan dan rekonsiliasi setelah memeriksa TikTok secara manual."""
import argparse
from pathlib import Path
import sqlite3
from content_queue import Claim, transaction, finish_success
from archive_used import archive_after_success

from project_paths import PROJECT_ROOT

DB_PATH=PROJECT_ROOT/'db/tiktok_bot.db'


def resolve(conn, content_id, result):
    if result in ('success','draft','scheduled'):
        row=conn.execute('SELECT claim_product_id,claimed_by,file_path,claim_token,status FROM contents WHERE id=?',(content_id,)).fetchone()
        if not row or row[4] not in ('submitting','uncertain'):raise ValueError('Video bukan submit tertahan')
        claim=Claim(content_id,row[0],row[1],row[2],row[3])
        from schedule_store import attach
        attach(conn)
        finish_success(conn,claim,{'success':'auto','draft':'manual','scheduled':'schedule'}[result])
        archive_after_success(conn,content_id)
        return
    with transaction(conn):
        row=conn.execute('SELECT status,submission_log_id FROM contents WHERE id=?',(content_id,)).fetchone()
        if not row or row[0] not in ('claimed','submitting','uncertain'):raise ValueError('Video tidak terkunci')
        if row[1]:conn.execute("UPDATE posts_log SET status='failed',error_message='Operator memeriksa: video tidak diposting/draft' WHERE id=?",(row[1],))
        conn.execute("UPDATE contents SET status='unused',claimed_by=NULL,claim_product_id=NULL,claim_token=NULL,claimed_at=NULL,submission_log_id=NULL WHERE id=?",(content_id,))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True);sub.add_parser('list')
    r=sub.add_parser('resolve');r.add_argument('content_id',type=int);r.add_argument('--result',choices=['success','draft','scheduled','not-posted'],required=True)
    r.add_argument('--checked-tiktok',action='store_true',help='Operator sudah memeriksa TikTok dan menghentikan proses akun terkait')
    args=parser.parse_args()
    with sqlite3.connect(DB_PATH.as_uri()+'?mode=rw',uri=True,timeout=30) as conn:
        if args.action=='list':
            for row in conn.execute('SELECT c.id,c.status,a.slug,c.file_path FROM contents c LEFT JOIN accounts a ON a.id=c.claimed_by ORDER BY c.id'):
                print(' | '.join(str(v or '-') for v in row))
        else:
            if not args.checked_tiktok:raise SystemExit('Periksa hasil TikTok dan hentikan proses akun sebelum rekonsiliasi; tambahkan --checked-tiktok setelah selesai.')
            resolve(conn,args.content_id,args.result)
            print('Rekonsiliasi selesai; tidak ada upload/submit baru.')


if __name__=='__main__':main()
