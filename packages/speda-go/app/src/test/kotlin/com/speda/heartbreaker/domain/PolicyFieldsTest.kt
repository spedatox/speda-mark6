package com.speda.heartbreaker.domain

import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import java.io.File

class PolicyFieldsTest {
    private val schema = Json.parseToJsonElement("""{
      "type":"object", "properties":{
        "run":{"${'$'}ref":"#/${'$'}defs/Run"},
        "lifetime_tokens":{"anyOf":[{"type":"integer","minimum":0},{"type":"null"}]},
        "prices":{"type":"object","additionalProperties":{"type":"number","exclusiveMinimum":0}},
        "fallbacks":{"type":"array","maxItems":3,"items":{"type":"string"}}
      }, "${'$'}defs":{"Run":{"type":"object","required":["tokens","calls"],"properties":{
        "tokens":{"type":"integer","minimum":0,"maximum":20000000},"calls":{"type":"integer","minimum":0}
      }}}
    }""").jsonObject
    @Test fun typedNumbersAndNestedUpdatesRetainOtherValues() {
        val original = Json.parseToJsonElement("""{"run":{"tokens":200000,"calls":16},"lifetime_tokens":null,"prices":{"existing":1},"fallbacks":["one","two"],"retained":"keep"}""").jsonObject
        val edited = JsonObject(original + ("run" to JsonObject(original.getValue("run").jsonObject + ("tokens" to PolicyFields.number("2000000", true)))))
        assertEquals(2000000, edited.getValue("run").jsonObject.getValue("tokens").jsonPrimitive.int)
        assertFalse(edited.getValue("run").jsonObject.getValue("tokens").jsonPrimitive.isString)
        assertEquals(original["prices"], edited["prices"])
        assertEquals(original["fallbacks"], edited["fallbacks"])
        assertEquals(original["retained"], edited["retained"])
        assertTrue(PolicyFields.validate(schema, schema, edited).isEmpty())
    }
    @Test fun invalidAndBlankNumbersCannotSave() {
        val tokenSchema = PolicyFields.resolve(schema.getValue("${'$'}defs").jsonObject.getValue("Run").jsonObject, schema).getValue("properties").jsonObject.getValue("tokens").jsonObject
        listOf("", "abc", "-1", "1.5", "20000001", "Infinity").forEach {
            assertTrue(it, PolicyFields.validate(tokenSchema, schema, PolicyFields.number(it, true)).isNotEmpty())
        }
    }
    @Test fun nullableUnionDoesNotAcceptInvalidNumberThroughNullBranch() {
        val nullable = schema.getValue("properties").jsonObject.getValue("lifetime_tokens").jsonObject
        assertTrue(PolicyFields.validate(nullable, schema, JsonNull).isEmpty())
        assertTrue(PolicyFields.validate(nullable, schema, JsonPrimitive(20)).isEmpty())
        assertTrue(PolicyFields.validate(nullable, schema, JsonPrimitive("")).isNotEmpty())
        assertTrue(PolicyFields.validate(nullable, schema, JsonPrimitive(-1)).isNotEmpty())
    }
    @Test fun mapsListsAndChoicesAreValidated() {
        val properties = schema.getValue("properties").jsonObject
        assertTrue(PolicyFields.validate(properties.getValue("prices").jsonObject, schema, Json.parseToJsonElement("""{"model":0}""")).isNotEmpty())
        assertTrue(PolicyFields.validate(properties.getValue("fallbacks").jsonObject, schema, Json.parseToJsonElement("""["a","b","c","d"]""")).isNotEmpty())
        val enum = Json.parseToJsonElement("""{"type":"string","enum":["off","effort"]}""").jsonObject
        assertTrue(PolicyFields.validate(enum, schema, JsonPrimitive("wrong")).isNotEmpty())
        assertEquals("Ayrılan token sınırı", PolicyFields.label("tokens", true))
        assertEquals("Reserved token limit", PolicyFields.label("tokens", false))
    }
    @Test fun realBackendPoliciesValidateWhenFixturesProvided() {
        val fixture = System.getenv("SPEDA_POLICY_FIXTURES") ?: return
        Json.parseToJsonElement(File(fixture).readText()).jsonArray.forEach {
            val policy = it.jsonObject
            val policySchema = policy.getValue("schema").jsonObject
            assertEquals(policy.getValue("id").jsonPrimitive.content, emptyList<String>(), PolicyFields.validate(policySchema, policySchema, policy.getValue("value")))
        }
    }
}
