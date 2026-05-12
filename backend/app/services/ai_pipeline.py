import datetime as _dt
import json
from app.core.config import settings
from app.services.openai_client import get_openai_client
from app.services.retriever import query_postgres_database
from app.services.memory_store import session_store
from app.services.realtime_service import _pick_combo_message


class _SafeJsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (_dt.datetime, _dt.date)):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return str(obj)


async def run_ai_pipeline(text: str, game_mode: str | None = None, session_id: str = "default") -> dict:
    """Uses OpenAI Function Calling with conversation memory (Redis-backed, in-memory fallback)."""
    stored = await session_store.get_session_data(session_id)
    all_msgs: list = stored.get("messages", [])

    # Keep only the last 10 messages for context so we don't blow up token limits
    context_msgs = all_msgs[-10:]
    
    # 1. Provide the exact SYSTEM ROLE instructions
    system_prompt = f"""SYSTEM ROLE

You are an AI assistant that answers combo validation questions using a PostgreSQL database.

Users may ask questions using voice in any language (English, Tamil, Malayalam, French, or mixed languages).
You must understand the intent and extract the numbers from the sentence.

The AI must only return information that exists in the database and must not invent answers.

---

DATABASE STRUCTURE

The PostgreSQL database contains a table called `card_combos`.
Current selected mode: {game_mode or 'OriginArc (Balakanda)'}

The table contains the following key columns:
gameplay_mode (e.g., 'OriginArc (Balakanda)', 'CrownShift (AyodhyaKanda)'), character, attribute, final_status, revised_scholar_reason, character_card_number, attribute_card_no

---

MODE PROGRESSION RULES

1. The game always starts with Mode 1 unlocked.
2. Mode 2 unlocks after Mode 1. Mode 3 after Mode 2.
3. Assume the user unlocked the current mode unless the DB query fails.

---

USER QUESTION TYPES

Users ask questions about combinations of two numbers.
Examples: "Is 1 and 29 a combo?", "Why is 1 and 29 valid?"

---

PROCESSING RULES

1. Extract the two numbers from the user's question, OR refer to numbers discussed previously in the conversation context.
2. Determine which mode the player has selected (Current Mode: {game_mode or 'Mode 1'}).
3. Query the 'card_combos' table for that mode by calling the 'query_database_for_combo' tool.
4. IF THE USER ASKS IF A COMBINATION EXISTS (e.g. "is 1 and 29 a combo?", "is it a combo?"):
   The tool result will contain a 'flavor_msg' and the card numbers 'character' and 'attribute'.
   Respond EXACTLY in this format: "Card {{character}} and {{attribute}} — {{flavor_msg}}"
   Translate this entire sentence into the user's language. Nothing else. No extra explanation.
5. IF THE USER ASKS WHY OR HOW (e.g. "why?", "how?", "ஏன்?"):
   Return ONLY the 'revised_scholar_reason' from the database translated into the exact language the user just used.
6. If the database result contains "both_character": true, respond ONLY with: "Two character card numbers can't be a combo." — translated into the user's language.
7. If the database result contains "both_attribute": true, respond ONLY with: "Two attribute card numbers can't be a combo." — translated into the user's language.
8. If the database result contains "character_not_in_mode": true, it means the character card does not exist in the selected mode at all. In this case, pick ONE message at random from the list below and respond with it translated into the user's language:
   - "Wrong mode. This character has no part in this chapter. Big card, wrong room."
   - "This character is not part of this mode's story. Not their chapter, not their moment."
   - "This character sat this mode out entirely. No role, no lines, no score."
   - "Not their era. The plot moved on without them for this one."
   - "Lore-accurate no-show. This character simply doesn't exist in this mode."
9. If no matching row exists but the character does exist in the mode, tell the user that specific combination is not valid.

IMPORTANT: You MUST detect the language of the CURRENT user query and respond in that EXACT same language. 
1. If the current query is in English (e.g., "Why?", "How?"), respond ONLY in English.
2. If the current query is in Tamil (e.g., "ஏன்?", "எப்படி?"), respond ONLY in Tamil.
3. If the current query is in Malayalam, respond ONLY in Malayalam. 
4. If the current query is in French, respond ONLY in French. 
5. Always TRANSLATE the validation reason from the database into the EXACT language of the latest query.

CRITICAL: Do not let previous messages in the chat history influence the language of your current response. If the history is in Tamil but the last question is "Why?", you must answer in English.
NEVER switch languages unless the user switches first."""

    tools = [
        {
            "type": "function",
            "function": {
                "name": "query_database_for_combo",
                "description": "Queries the card_combos table for combo status and reason.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "description": "The gameplay_mode exactly as provided (e.g. 'OriginArc (Balakanda)', 'CrownShift (AyodhyaKanda)', 'WildRun (AranyaKanda)', 'GlowLine (KishkindhaKanda)', 'lankaLeap (SundaraKanda)', 'WarRoom (YuddhaKanda)', 'AfterLight (UttaraKanda)')."},
                        "character": {"type": "string", "description": "The character name or card number ID."},
                        "attribute": {"type": "string", "description": "The attribute card number (25+)."}
                    },
                    "required": ["mode", "character", "attribute"]
                }
            }
        }
    ]
    
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(context_msgs)
    messages.append({"role": "user", "content": text})
    
    try:
        client = get_openai_client()
        # 2. Call OpenAI with the database tool
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        
        message = response.choices[0].message
        
        # 3. If the AI decides it needs to query the database, execute the query!
        llm_selected_mode = "N/A (No tool call)"
        if message.tool_calls:
            # We must append the assistant's request for tools before the tool results
            messages.append(message)
            
            for tool_call in message.tool_calls:
                if tool_call.function.name == "query_database_for_combo":
                    args = json.loads(tool_call.function.arguments)
                    llm_selected_mode = args.get('mode', 'Mode 1')
                    # Force use of the backend-received game_mode to ensure grounding in the selected mode
                    target_mode = game_mode or llm_selected_mode
                    db_result = await query_postgres_database(
                        target_mode,
                        args.get('character'),
                        args.get('attribute')
                    )

                    # Inject flavor_msg + card numbers so AI formats response as "Card X and Y — [msg]"
                    db_parsed = json.loads(db_result) if isinstance(db_result, str) else db_result
                    flavor = _pick_combo_message(db_parsed.get("status"), is_error=False)
                    db_parsed["flavor_msg"] = flavor
                    db_parsed["character"] = args.get("character")
                    db_parsed["attribute"] = args.get("attribute")

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.function.name,
                        "content": json.dumps(db_parsed, cls=_SafeJsonEncoder)
                    }
                    messages.append(tool_msg)
            
            # 4. Generate the final response grounded in the DB results and translated
            final_response = await get_openai_client().chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=messages
            )
            final_text = final_response.choices[0].message.content or ""
        else:
            final_text = message.content or ""
            
        # --- TERMINAL LOGGING ---
        print("\n" + "="*50, flush=True)
        print(f"USER SELECTED MODE : {game_mode}", flush=True)
        print(f"QUESTION           : {text}", flush=True)
        print(f"LLM SELECTED MODE  : {llm_selected_mode}", flush=True)
        print(f"RESPONSE           : {final_text}", flush=True)
        print("="*50 + "\n", flush=True)
        
        # 5. Persist text-only history (trim to last 20 to prevent unbounded growth)
        all_msgs.append({"role": "user", "content": text})
        all_msgs.append({"role": "assistant", "content": final_text})
        trimmed = all_msgs[-20:]
        await session_store.set_session_data(session_id, {"messages": trimmed})
        
        return {"final_response": final_text}

    except Exception as e:
        print(f"--- AI PIPELINE ERROR ---")
        print(f"Error: {e}")
        # Log the messages list to see exactly what failed for debugging
        # print(f"Messages sent: {json.dumps(messages, indent=2)}")
        return {"final_response": f"Sorry, I encountered an internal error: {str(e)}"}
