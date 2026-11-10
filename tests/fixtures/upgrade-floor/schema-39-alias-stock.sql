-- Derived on a private schema-39 copy from the three floor-written buddy rows.
-- Alpha retains its proven synthetic description. Beta gets a recorded native
-- description; Gamma loses its current catalog provenance but remains proven
-- by an older payload. An orphan derived from Gamma has no catalog model.
INSERT INTO evaluation_catalog(discovery_id,payload_json)
SELECT 'fixture-native-beta',
       json_set(payload_json, '$.providers[0].models[1].description', 'Native beta description')
FROM evaluation_catalog WHERE discovery_id='cat-89b5b236ee44be1428bb5c86';
UPDATE buddies SET source='catalog:fixture-native-beta',
                   description='Native beta description'
WHERE buddy_id='codex:openai:fixture-beta:high';
UPDATE buddies SET source='catalog:fixture-missing-gamma'
WHERE buddy_id='codex:openai:fixture-gamma:high';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,
                    enabled,capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:fixture-orphan:high','fixture-orphan · high',harness,
       provider,'fixture-orphan',effort,available,enabled,capabilities_json,
       context_window,'fixture-orphan via openai','catalog:fixture-missing-orphan',
       unavailable_code,unavailable_params_json,unavailable_content
FROM buddies WHERE buddy_id='codex:openai:fixture-gamma:high';
-- These cases are derived from Gamma's floor-written identity and columns.
-- The original floor writer supplied the catalog payload and buddy rows;
-- schema-39 status writers could later overwrite their availability columns.
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,
                    enabled,capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:fixture-alpha:medium','fixture-alpha · medium',harness,
       provider,'fixture-alpha','medium',0,enabled,
       '["execution:codex","effort:medium","input:text"]',context_window,
       description,source,'catalog.effort-retired','{}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
UPDATE buddies SET description='Stale alternate detail',context_window=4096,
                   capabilities_json='["execution:codex","effort:medium","input:image"]',
                   source='catalog:cat-89b5b236ee44be1428bb5c86'
WHERE buddy_id='codex:openai:fixture-alpha:medium';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,
                    enabled,capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:fixture-alpha:low','fixture-alpha · low',harness,
       provider,'fixture-alpha','low',0,enabled,
       '["execution:codex","effort:low","input:text"]',context_window,
       description,source,'harness.unhealthy','{}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-alpha:high';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,
                    enabled,capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:fixture-native:high','fixture-native · high',harness,
       provider,'fixture-native',effort,0,enabled,capabilities_json,context_window,
       'Native model detail',source,'catalog.native-unavailable','{"detail":"native"}',
       'Native diagnostic'
FROM buddies WHERE buddy_id='codex:openai:fixture-gamma:high';
INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,
                    enabled,capabilities_json,context_window,description,source,
                    unavailable_code,unavailable_params_json,unavailable_content)
SELECT 'codex:openai:fixture-absent:high','fixture-absent · high',harness,
       provider,'fixture-absent',effort,0,enabled,capabilities_json,context_window,
       'Remembered absent model',source,'catalog.model-absent','{}',NULL
FROM buddies WHERE buddy_id='codex:openai:fixture-gamma:high';
INSERT INTO model_service_concurrency(harness,provider,model,concurrency_limit)
VALUES('codex','openai','fixture-native',5);
