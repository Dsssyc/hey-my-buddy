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
