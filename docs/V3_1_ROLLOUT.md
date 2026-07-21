# V3.1 production rollout

V3.1 is additive and preserves the Telegram bot, the 3 PM Kuwait delivery contract, the Render dashboard URL, existing Supabase data, and the JSON fallback. Live order execution remains absent.

## Activation order

1. Validate Python compilation, unit tests, dashboard APIs, Supabase access, and limited live-data scans on the draft pull request.
2. Deploy the backward-compatible `telegram-commands` Edge Function. Before the migration it uses the legacy configuration rows; afterward it reads Supabase Vault.
3. Apply `202607210002_v3_1_hardening.sql`. This creates the atomic queue, run records, revocable dashboard sessions, maintenance RPC, and Vault-backed Telegram configuration.
4. Verify RLS/advisors, the Vault RPC, queue RPCs, and Edge Function `/status` behavior.
5. Merge the validated pull request into `main`.
6. Confirm the GitHub validation workflow, Telegram command activation, and Render auto-deploy are green.
7. Run controlled dry scans and a queue round trip. Do not send synthetic trading alerts.

## Rollback

- Set `DELIVERY_BACKEND=local` to stop database queue use while retaining the JSON queue.
- Revert the application merge to restore the prior scanner and dashboard code.
- Leave the additive database objects in place during application rollback; they do not change existing base-table contracts and preserve diagnostic records.
- Redeploy the prior Telegram Edge Function only if command handling itself regresses.

Dropping V3.1 tables or deleting existing signals is not part of normal rollback.
