import { useCallback, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "./api";
import { Notice, useConfirm, type ConfirmRequest } from "./components";

/** A database a new job would read that another live ingestion job reads too (main.py _shared_verdict). */
export interface SharedDatabase {
  database: string;
  job_id: number;
  job: string;
  /** The new job's target for it still needs a table's schema or name, so the server checks it at the write. */
  unchecked: boolean;
}

/** What the shared-database rule says about a Create's preview: what it refuses (a target catalog or schema the other
 *  job also writes, which no confirmation helps), the jobs those name, and the databases a Create would share. */
export interface SharedVerdict {
  refused: string[];
  jobs: { id: number; name: string }[];
  shares: SharedDatabase[];
}

/** The databases a Create's refusal asks the user to confirm (a 409 with confirm_shared); null for any other error. */
export function sharedConfirmation(error: unknown): SharedDatabase[] | null {
  const detail = error instanceof ApiError ? error.detail : undefined;
  return detail?.confirm_shared ? (detail.shares as SharedDatabase[]) : null;
}

/** The question a Create asks before it makes a job that reads databases another live job reads, as the Databricks UI
 *  asks before a change with a cost: what is shared, with whom, and what it costs the source. */
export function shareQuestion(shares: SharedDatabase[], action = "Continue"): ConfirmRequest {
  return { title: "Read these databases from a second job?", action, body: <SharedList shares={shares} /> };
}

/** Sends a Create. When the server asks to confirm databases another job reads (because the preview the page asked
 *  first did not list them yet), asks the user and sends it again with confirm_shared set. A declined question throws
 *  the server's answer back to the caller. */
export async function confirmingShared<T>(ask: (request: ConfirmRequest) => Promise<boolean>,
                                          send: (confirmShared: boolean) => Promise<T>): Promise<T> {
  try {
    return await send(false);
  } catch (error) {
    const shares = sharedConfirmation(error);
    if (!shares || !(await ask(shareQuestion(shares)))) throw error;
    return send(true);
  }
}

/** For a page whose write may give a job a database another live job reads: send(post) sends the write and, when the
 *  server asks, asks the user and sends it again with confirm_shared (confirmingShared); render dialog on the page. */
export function useSharedConfirm(): { send: <T>(post: (confirmShared: boolean) => Promise<T>) => Promise<T>;
                                      dialog: ReactNode } {
  const confirm = useConfirm();
  const ask = confirm.ask;
  const send = useCallback(<T,>(post: (confirmShared: boolean) => Promise<T>) => confirmingShared(ask, post), [ask]);
  return { send, dialog: confirm.dialog };
}

/** The databases a second job would read, each with the live job that reads it now, linked. */
export function SharedList({ shares }: { shares: SharedDatabase[] }) {
  return (
    <>
      <p>
        Another live ingestion job already reads {shares.length === 1 ? "this database" : "these databases"}. This job
        reads {shares.length === 1 ? "it" : "them"} too, on its own schedule and into its own catalog and schema, so
        the source is read once more for each job. That job is not changed.
      </p>
      <ul>
        {shares.slice(0, 20).map((share) => (
          <li key={`${share.job_id}-${share.database}`}>
            <b>{share.database}</b>, read by <Link to={`/ingestion-jobs/${share.job_id}`}>{share.job}</Link>
            {share.unchecked ? " (its target names a table's schema or name, so it is checked as the tables are written)" : ""}
          </li>
        ))}
      </ul>
      {shares.length > 20 && <p className="muted">and {shares.length - 20} more.</p>}
    </>
  );
}

/** A Create preview's shared databases: what it refuses, with a link to each job named, and what it would share,
 *  which Create then asks to confirm. Nothing when the rule has nothing to say. */
export function SharedNotice({ verdict }: { verdict?: SharedVerdict | null }) {
  if (!verdict) return null;
  return (
    <>
      {verdict.refused.length > 0 && (
        <Notice tone="bad">
          <ul>{verdict.refused.slice(0, 20).map((reason) => <li key={reason}>{reason}</li>)}</ul>
          {verdict.jobs.map((job) => (
            <div className="hint" key={job.id}>Open ingestion job <Link to={`/ingestion-jobs/${job.id}`}>{job.name}</Link></div>
          ))}
        </Notice>
      )}
      {verdict.shares.length > 0 && <Notice tone="warn"><SharedList shares={verdict.shares} /></Notice>}
    </>
  );
}
