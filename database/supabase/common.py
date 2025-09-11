# database/supabase/common.py
from typing import Iterable, List, Dict, Any, Optional
from database.supabase.connection import get_supabase

def upsert_rows(
    table: str,
    rows: Iterable[Dict[str, Any]],
    on_conflict: Optional[str] = None,
    ignore_duplicates: bool = False,
) -> List[Dict[str, Any]]:
    """
    rows: iterable of dicts; keys повинні співпасти з колонками.
    on_conflict: ім'я унікального індексу/колонки(ок), напр. "id" або "symbol,timeframe,ts".
    """
    sb = get_supabase()
    payload = list(rows)
    if not payload:
        return []
    q = sb.table(table).upsert(payload)
    if on_conflict:
        q = q.on_conflict(on_conflict)
    if ignore_duplicates:
        q = q.ignore_duplicates()
    resp = q.execute()
    return resp.data or []

def insert_rows(table: str, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sb = get_supabase()
    payload = list(rows)
    if not payload:
        return []
    resp = sb.table(table).insert(payload).execute()
    return resp.data or []

def select_rows(
    table: str,
    eq: Optional[Dict[str, Any]] = None,
    order: Optional[str] = None,
    desc: bool = True,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    sb = get_supabase()
    q = sb.table(table).select("*")
    if eq:
        for k, v in eq.items():
            q = q.eq(k, v)
    if order:
        q = q.order(order, desc=desc)
    if limit:
        q = q.limit(limit)
    resp = q.execute()
    return resp.data or []
