-- Multi-effort catalog stock derived from the floor-written alpha row.
-- The payload copies replace only model metadata in a retained floor payload.
-- Later statements follow the old writers: retirement/health changes a cause;
-- disappearance confirmation changes only rows that are still available.
INSERT INTO evaluation_catalog(discovery_id,payload_json)
SELECT 'catalog-stock-active',json_set(payload_json,'$.providers[0].efforts',json('["high","xhigh"]'),'$.providers[0].models',json('[{"id":"stock-absent-retired","name":"stock-absent-retired","description":"Confirmed model detail","contextWindow":65536,"inputModalities":["text"],"efforts":["high"],"available":true,"unavailableReason":null,"unavailableContent":null},{"id":"stock-pending-retired","name":"stock-pending-retired","description":"Current pending detail","contextWindow":32768,"inputModalities":["text","image"],"efforts":["high"],"available":true,"unavailableReason":null,"unavailableContent":null},{"id":"stock-confirmed-health","name":"stock-confirmed-health","description":"Confirmed health detail","contextWindow":8192,"inputModalities":["text"],"efforts":["high","xhigh"],"available":true,"unavailableReason":null,"unavailableContent":null},{"id":"stock-health-only","name":"stock-health-only","description":"Health masked detail","contextWindow":16384,"inputModalities":["text"],"efforts":["high"],"available":true,"unavailableReason":null,"unavailableContent":null},{"id":"stock-all-retired","name":"stock-all-retired","description":"Old retired detail","contextWindow":2048,"inputModalities":["text"],"efforts":["high","xhigh"],"available":true,"unavailableReason":null,"unavailableContent":null}]'))
FROM evaluation_catalog WHERE discovery_id='cat-89b5b236ee44be1428bb5c86';
INSERT INTO evaluation_catalog(discovery_id,payload_json)
SELECT 'catalog-stock-stale',json_set(payload_json,'$.providers[0].efforts',json('["high","xhigh"]'),'$.providers[0].models',json('[{"id":"stock-absent-retired","name":"stock-absent-retired","description":"Stale retired detail","contextWindow":4096,"inputModalities":["audio"],"efforts":["high","xhigh"],"available":true,"unavailableReason":null,"unavailableContent":null},{"id":"stock-pending-retired","name":"stock-pending-retired","description":"Stale retired detail","contextWindow":4096,"inputModalities":["audio"],"efforts":["high","xhigh"],"available":false,"unavailableReason":{"code":"catalog.native-unavailable","params":{}},"unavailableContent":"Stale native limitation"},{"id":"stock-confirmed-health","name":"stock-confirmed-health","description":"Stale retired detail","contextWindow":4096,"inputModalities":["audio"],"efforts":["high","xhigh"],"available":false,"unavailableReason":{"code":"catalog.native-unavailable","params":{}},"unavailableContent":"Stale native limitation"},{"id":"stock-health-only","name":"stock-health-only","description":"Stale retired detail","contextWindow":4096,"inputModalities":["audio"],"efforts":["high","xhigh"],"available":false,"unavailableReason":{"code":"catalog.native-unavailable","params":{}},"unavailableContent":"Stale native limitation"},{"id":"stock-all-retired","name":"stock-all-retired","description":"Stale retired detail","contextWindow":4096,"inputModalities":["audio"],"efforts":["high","xhigh"],"available":false,"unavailableReason":{"code":"catalog.native-unavailable","params":{}},"unavailableContent":"Stale native limitation"}]'))
FROM evaluation_catalog WHERE discovery_id='cat-89b5b236ee44be1428bb5c86';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:stock-absent-retired:high','stock-absent-retired · high','codex',provider,
       'stock-absent-retired','high',1,enabled,'["execution:codex","effort:high","input:text"]',65536,'Confirmed model detail',
       'catalog:catalog-stock-active',NULL,NULL,NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:stock-absent-retired:xhigh','stock-absent-retired · xhigh','codex',provider,
       'stock-absent-retired','xhigh',0,enabled,'["execution:codex","effort:xhigh","input:audio"]',4096,'Stale retired detail',
       'catalog:catalog-stock-stale','catalog.effort-retired','{}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
UPDATE buddies SET available=0,unavailable_code='catalog.model-absent',
                   unavailable_params_json='{}',unavailable_content=NULL
WHERE harness='codex' AND provider='openai' AND model='stock-absent-retired' AND available=1;
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:stock-pending-retired:high','stock-pending-retired · high','codex',provider,
       'stock-pending-retired','high',1,enabled,'["execution:codex","effort:high","input:text","input:image"]',32768,'Current pending detail',
       'catalog:catalog-stock-active',NULL,NULL,NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:stock-pending-retired:xhigh','stock-pending-retired · xhigh','codex',provider,
       'stock-pending-retired','xhigh',0,enabled,'["execution:codex","effort:xhigh","input:audio"]',4096,'Stale retired detail',
       'catalog:catalog-stock-stale','catalog.effort-retired','{}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:stock-confirmed-health:high','stock-confirmed-health · high','codex',provider,
       'stock-confirmed-health','high',1,enabled,'["execution:codex","effort:high","input:text"]',8192,'Confirmed health detail',
       'catalog:catalog-stock-active',NULL,NULL,NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:stock-confirmed-health:xhigh','stock-confirmed-health · xhigh','codex',provider,
       'stock-confirmed-health','xhigh',0,enabled,'["execution:codex","effort:xhigh","input:audio"]',4096,'Stale retired detail',
       'catalog:catalog-stock-stale','harness.probe-failed','{"operation":"probe"}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
UPDATE buddies SET available=0,unavailable_code='catalog.model-absent',
                   unavailable_params_json='{}',unavailable_content=NULL
WHERE harness='codex' AND provider='openai' AND model='stock-confirmed-health' AND available=1;
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:stock-health-only:high','stock-health-only · high','codex',provider,
       'stock-health-only','high',0,enabled,'["execution:codex","effort:high","input:text"]',16384,'Health masked detail',
       'catalog:catalog-stock-active','harness.probe-failed','{"operation":"probe"}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:stock-all-retired:high','stock-all-retired · high','codex',provider,
       'stock-all-retired','high',0,enabled,'["execution:codex","effort:high","input:text"]',2048,'Old retired detail',
       'catalog:catalog-stock-active','catalog.effort-retired','{}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:stock-all-retired:xhigh','stock-all-retired · xhigh','codex',provider,
       'stock-all-retired','xhigh',0,enabled,'["execution:codex","effort:xhigh","input:audio"]',1024,'Last retired detail',
       'catalog:catalog-stock-stale','catalog.effort-retired','{}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
INSERT INTO catalog_pending(harness,provider,model,since,efforts_json)
VALUES('codex','openai','stock-pending-retired','2026-01-01T00:02:00.000Z','["high"]');
INSERT INTO events(kind,payload_json,created_at)
VALUES('catalog.model_unavailable',
       '{"harness":"codex","provider":"openai","model":"stock-absent-retired","pendingSince":"2025-12-31T23:01:30.000Z","confirmedAt":"2026-01-01T00:01:30.000Z"}',
       '2026-01-01T00:01:30.000Z');
-- A confirmed cause survives even when its current reading and event are gone.
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'claude:openai:stock-no-time:high','stock-no-time · high','claude',provider,
       'stock-no-time','high',0,enabled,'["execution:claude","effort:high","input:text"]',8192,'No-time confirmed detail',
       'catalog:catalog-stock-active','catalog.model-absent','{}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,
                    capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'claude:openai:stock-no-time:xhigh','stock-no-time · xhigh','claude',provider,
       'stock-no-time','xhigh',0,enabled,'["execution:claude","effort:xhigh","input:audio"]',4096,'Stale no-time detail',
       'catalog:catalog-stock-active','catalog.effort-retired','{}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
