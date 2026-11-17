-- New form introduced by the schema-31 maintenance writer at bad89c7b.
-- Applied only after the frozen 30 -> 31 step, never to the schema-30 stock.
INSERT INTO tasks(task_id,request_id,owner,spec_json,input_fingerprint,fingerprint_version,adapter,required_capabilities,cwd,exclusive_resources,expected_seconds,router_budget_seconds,state,revision,created_at,updated_at)
VALUES('schema31-maintenance-task','decision:maint-schema31-stock','decision','{"adapter":"decision","cwd":"<fixture-maintenance-cwd>","decision":{"decisionId":"maint-schema31-stock","document":{"buddy":{"effort":"high","harness":"codex","model":"fixture-alpha","provider":"openai"},"buddyId":"codex:openai:fixture-alpha:high","budget":{"bytesRead":524288,"preset":"standard","timeoutSeconds":300,"toolCalls":24},"input":{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"kind":"maintain","maintenanceRunId":"maint-schema31-stock","routingMode":"fast"},"kind":"maintain","routerBudgetSeconds":300},"requiredCapabilities":["decision"],"routerBudgetSeconds":310,"task":"Maintain domains, task classifications and buddy kind summaries."}','17cb152e15f6dd1ad617da5fe07a77b76761da495c5dbc7ba4f9900fc4442cd4',2,'decision','["decision"]','<fixture-maintenance-cwd>','[]',NULL,310,'completed',1,'2026-01-01T00:00:00.000Z','2026-01-01T00:00:00.000Z');
INSERT INTO maintenance_runs(maintenance_run_id,trigger_type,buddy_id,task_id,state,started_at,finished_at)
VALUES('maint-schema31-stock','manual','codex:openai:fixture-alpha:high','schema31-maintenance-task','completed','2026-01-01T00:00:00.000Z','2026-01-01T00:00:00.000Z');

-- The floor stock has POSIX identities but none for Windows. Reproduce the
-- Windows record_identity row shape from 4de1dd94 on an ended floor attempt.
INSERT INTO attempt_process_identities(attempt_id,boot_id,root_pid,root_started_at,process_group_id,job_name)
VALUES('00000000-0000-0000-0000-000000000004',NULL,2147483645,1,NULL,'Local\HeyMyBuddy-00000000-0000-4000-8000-000000000001');

-- Floor results omit the null usage counter and the budget copy. Reproduce
-- buddy/roles/router.py at 37e3fc97 on the ended review result, using its frozen
-- deep budget. Sealed input, snapshot and receipt bytes stay untouched.
UPDATE attempts SET result_json=json_set(result_json,'$.result.usage.bytesRead',NULL,
'$.result.budget',json('{"preset":"deep","timeoutSeconds":600,"toolCalls":64,"bytesRead":2097152}'))
WHERE attempt_id='00000000-0000-0000-0000-000000000004';
UPDATE decision_requests SET output_json=json_set(output_json,'$.usage.bytesRead',NULL,
'$.budget',json('{"preset":"deep","timeoutSeconds":600,"toolCalls":64,"bytesRead":2097152}'))
WHERE decision_id='dec-00000000-0000-0000-0000-000000000002';
