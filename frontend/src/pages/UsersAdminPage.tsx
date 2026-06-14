import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, RefreshCw, Wallet } from "lucide-react";
import { listUsers, updateUserCap, type UserUsageRow } from "../api/users";

function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

// Admin surface for per-user weekly usage caps (DESIGN.md §5 2026-06-14).
// Architect-gated server-side; caps are shown and entered in credits.
export function UsersAdminPage() {
  const [items, setItems] = useState<UserUsageRow[]>([]);
  const [defaultCap, setDefaultCap] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingOid, setSavingOid] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    listUsers()
      .then((r) => {
        setItems(r.items);
        setDefaultCap(r.default_cap_credits);
        setDrafts({});
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const applyRow = (row: UserUsageRow) =>
    setItems((prev) => prev.map((it) => (it.oid === row.oid ? row : it)));

  const save = async (oid: string, raw: string | null) => {
    setSavingOid(oid);
    setError(null);
    try {
      let capCredits: number | null;
      if (raw === null || raw.trim() === "") {
        capCredits = null; // clear → default
      } else if (Number.isNaN(Number(raw))) {
        throw new Error("Cap must be a number");
      } else {
        capCredits = Math.max(0, Math.round(Number(raw)));
      }
      const updated = await updateUserCap(oid, capCredits);
      applyRow(updated);
      setDrafts((d) => {
        const next = { ...d };
        delete next[oid];
        return next;
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSavingOid(null);
    }
  };

  return (
    <div className="min-h-screen bg-base-950 text-base-100">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="text-base-400 hover:text-base-200 transition-colors"
              aria-label="Back to chat"
            >
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <Wallet className="w-5 h-5 text-accent-light" />
            <h1 className="text-lg font-semibold">Weekly usage caps</h1>
          </div>
          <button
            type="button"
            onClick={load}
            className="flex items-center gap-1.5 text-sm text-base-400 hover:text-base-200 transition-colors"
            aria-label="Refresh users"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>

        <p className="text-sm text-base-400 mb-5">
          Caps are in credits (1 credit = $0.01). Users with no override use the
          default of <span className="text-base-200">{fmt(defaultCap)}</span>{" "}
          credits/week. Clear a cap to revert to the default.
        </p>

        {error && (
          <div className="bg-danger/10 border border-danger/30 rounded-xl px-4 py-3 text-danger text-sm mb-4">
            {error}
          </div>
        )}

        {loading ? (
          <p className="text-base-500 text-sm">Loading…</p>
        ) : items.length === 0 ? (
          <p className="text-base-500 text-sm">No users yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-base-800">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wide text-base-500 border-b border-base-800">
                  <th className="px-4 py-2.5">User</th>
                  <th className="px-4 py-2.5 text-right">Spent / wk</th>
                  <th className="px-4 py-2.5 text-right">Remaining</th>
                  <th className="px-4 py-2.5 w-[280px]">Weekly cap (credits)</th>
                </tr>
              </thead>
              <tbody>
                {items.map((u) => {
                  const draft =
                    drafts[u.oid] ?? (u.cap_credits === null ? "" : String(u.cap_credits));
                  const busy = savingOid === u.oid;
                  return (
                    <tr key={u.oid} className="border-b border-base-800/60 last:border-0">
                      <td className="px-4 py-3">
                        <div className="text-base-100">{u.display_name}</div>
                        <div className="text-xs text-base-500">{u.email}</div>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-base-300">
                        {fmt(u.spent_this_week_credits)}
                      </td>
                      <td
                        className={`px-4 py-3 text-right tabular-nums ${
                          u.remaining_credits <= 0 ? "text-danger" : "text-base-200"
                        }`}
                      >
                        {fmt(u.remaining_credits)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            min={0}
                            value={draft}
                            placeholder={`Default (${fmt(defaultCap)})`}
                            onChange={(e) =>
                              setDrafts((d) => ({ ...d, [u.oid]: e.target.value }))
                            }
                            className="w-28 bg-base-900 border border-base-700 rounded-lg px-2.5 py-1.5 text-base-100 text-sm focus:outline-none focus:border-accent-light disabled:opacity-50"
                            disabled={busy}
                            aria-label={`Weekly cap for ${u.email}`}
                          />
                          <button
                            type="button"
                            onClick={() => save(u.oid, draft)}
                            disabled={busy}
                            className="px-3 py-1.5 rounded-lg bg-accent text-white text-sm hover:brightness-110 disabled:opacity-50 transition-[filter]"
                          >
                            Save
                          </button>
                          <button
                            type="button"
                            onClick={() => save(u.oid, null)}
                            disabled={busy || u.cap_credits === null}
                            className="px-2.5 py-1.5 rounded-lg text-base-400 hover:text-base-200 text-sm disabled:opacity-30 transition-colors"
                            title="Revert to default"
                          >
                            Clear
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
