/**
 * `GET /v1/templates` for the console tests: all eight v1 starters
 * (docs/v4/TEMPLATES.md §5) with their pack manifests, exactly as the api's
 * `templates.router.gallery()` serialises them (none-valued fields dropped).
 * Regenerate from the api rather than editing by hand when the catalogue
 * changes; the ids are pinned by api/tests/test_templates.py.
 */
import type { TemplateOut, TemplatesResponse } from "@/contracts/lkap-contracts";

export const TEMPLATES: TemplatesResponse = {
  "items": [
    {
      "template": {
        "v": 1,
        "id": "blank",
        "name": "Blank agent",
        "tagline": "A plain voice assistant on LiveKit Inference. Start here and build it your way.",
        "description": "Exactly what the generic pack seeds: a plain voice assistant on LiveKit Inference with the four default panel blocks and the built-in tools. The fastest path to a first test call.",
        "category": "blank",
        "chips": [],
        "requires": {
          "provider_keys": [],
          "telephony": false,
          "webhook_endpoint": false,
          "storage": false
        },
        "order": 0,
        "pack_id": "generic",
        "default_voice": {},
        "http_request_enabled": false,
        "tool_seeds": [],
        "kb_seeds": [],
        "pack_settings": {},
        "sample_prompts": [
          "What can you help me with?",
          "Tell me a fun fact.",
          "Can you speak more slowly?"
        ],
        "next_steps": [
          {
            "label": "Write the instructions",
            "section": "instructions"
          },
          {
            "label": "Check providers",
            "section": "providers"
          },
          {
            "label": "Make a test call from the editor header",
            "section": "providers"
          }
        ]
      },
      "pack": {
        "v": 1,
        "id": "generic",
        "version": "0.1.0",
        "name": "Generic assistant",
        "description": "A plain voice assistant with no pack-specific tools or UI state.",
        "ui_panel_id": "generic",
        "default_panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Notes",
              "config": {},
              "order": 1
            },
            {
              "id": "checklist",
              "type": "checklist",
              "title": "Still needed",
              "config": {},
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "blocks": [],
        "default_instructions": "You are a helpful voice assistant. Answer questions clearly and briefly, and ask a clarifying question when the user's request is ambiguous.",
        "default_greeting": "Hello! How can I help you today?",
        "default_voice": {},
        "recommended_pipeline": {
          "mode": "cascaded",
          "stt": {
            "provider_id": "livekit-inference-stt",
            "model": "deepgram/nova-3",
            "fields": {}
          },
          "llm": {
            "provider_id": "livekit-inference-llm",
            "model": "google/gemma-4-31b-it",
            "fields": {}
          },
          "tts": {
            "provider_id": "livekit-inference-tts",
            "model": "inworld/inworld-tts-2",
            "fields": {}
          },
          "avatar_options": {
            "participant_name": "Avatar"
          },
          "turn_handling": {}
        },
        "capabilities": {
          "camera": false,
          "screen_share": false,
          "chat_input": true,
          "vision_inject_per_turn": true,
          "dtmf": false
        },
        "builtin_tools_disabled": [],
        "tool_names": [],
        "state_schema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        },
        "settings_schema": {
          "type": "object"
        },
        "kb_seeds": [],
        "instructions_by_mode": {}
      },
      "derived": false
    },
    {
      "template": {
        "v": 1,
        "id": "knowledge_assistant",
        "name": "Knowledge assistant",
        "tagline": "Answers from your documents, cites its sources and hands off when it can't help.",
        "description": "A support agent that answers only from the knowledge bases you give it, names the source it used, admits what it does not know and offers a human handoff. Seeded with a sample product FAQ and a support playbook for a fictional product, Acme Meter.",
        "category": "support",
        "chips": [
          "rag",
          "citations"
        ],
        "requires": {
          "provider_keys": [],
          "telephony": false,
          "webhook_endpoint": false,
          "storage": false
        },
        "order": 10,
        "pack_id": "generic",
        "instructions": "You are the support assistant for Acme Meter, a smart energy meter and its companion app. You answer customers' questions by voice.\n\nHow you answer:\n- Answer only from the passages retrieved from the knowledge bases (they are added to the conversation automatically; call search_knowledge when you need more). Never answer from general knowledge or guesswork.\n- When you use a passage, name its source in plain words, for example \"According to the product FAQ, ...\".\n- If the answer is not in the sources, say \"I don't have that in my notes\" and offer to pass the question on. Do not guess, and do not make up prices, dates, policies or features.\n- Keep answers to two or three short sentences. Offer more detail instead of reading out long lists.\n- Follow the support playbook for tone, escalation and what never to promise.\n\nStatus and follow-ups:\n- Call set_status with \"researching\" while you look something up and \"answered\" once you have answered.\n- When the customer asks for something you cannot do in this call, call push_note with a one-line follow-up.\n\nHanding off:\n- Call escalate_to_human when the customer asks for a person, is upset, or needs something the sources do not cover (a refund decision, an account change, a billing dispute). Tell them a colleague will follow up.",
        "greeting": "Hi, I'm the support assistant. Ask me anything about the product and I'll answer from our documentation.",
        "builtin_tools_disabled": [
          "pin_frame",
          "describe_current_frame"
        ],
        "default_voice": {},
        "panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "sources",
              "type": "kb_citations",
              "title": "Sources",
              "config": {},
              "order": 1
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Follow-ups",
              "config": {},
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "http_request_enabled": false,
        "tool_seeds": [],
        "kb_seeds": [
          {
            "kb_name": "Knowledge assistant · Product FAQ",
            "files": [
              "product_faq.md"
            ]
          },
          {
            "kb_name": "Knowledge assistant · Support playbook",
            "files": [
              "support_playbook.md"
            ]
          }
        ],
        "knowledge": {
          "auto_inject": true,
          "top_k": 4
        },
        "pack_settings": {},
        "sample_prompts": [
          "What's your refund policy?",
          "Which plan includes API access?",
          "I need to talk to a person."
        ],
        "next_steps": [
          {
            "label": "Replace the sample FAQ with your documents",
            "section": "knowledge"
          },
          {
            "label": "Tune the instructions",
            "section": "instructions"
          },
          {
            "label": "Make a test call",
            "section": "providers"
          }
        ]
      },
      "pack": {
        "v": 1,
        "id": "generic",
        "version": "0.1.0",
        "name": "Generic assistant",
        "description": "A plain voice assistant with no pack-specific tools or UI state.",
        "ui_panel_id": "generic",
        "default_panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Notes",
              "config": {},
              "order": 1
            },
            {
              "id": "checklist",
              "type": "checklist",
              "title": "Still needed",
              "config": {},
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "blocks": [],
        "default_instructions": "You are a helpful voice assistant. Answer questions clearly and briefly, and ask a clarifying question when the user's request is ambiguous.",
        "default_greeting": "Hello! How can I help you today?",
        "default_voice": {},
        "recommended_pipeline": {
          "mode": "cascaded",
          "stt": {
            "provider_id": "livekit-inference-stt",
            "model": "deepgram/nova-3",
            "fields": {}
          },
          "llm": {
            "provider_id": "livekit-inference-llm",
            "model": "google/gemma-4-31b-it",
            "fields": {}
          },
          "tts": {
            "provider_id": "livekit-inference-tts",
            "model": "inworld/inworld-tts-2",
            "fields": {}
          },
          "avatar_options": {
            "participant_name": "Avatar"
          },
          "turn_handling": {}
        },
        "capabilities": {
          "camera": false,
          "screen_share": false,
          "chat_input": true,
          "vision_inject_per_turn": true,
          "dtmf": false
        },
        "builtin_tools_disabled": [],
        "tool_names": [],
        "state_schema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        },
        "settings_schema": {
          "type": "object"
        },
        "kb_seeds": [],
        "instructions_by_mode": {}
      },
      "derived": false
    },
    {
      "template": {
        "v": 1,
        "id": "receptionist",
        "name": "Receptionist",
        "tagline": "Books appointments through a flow, an on-screen form and two HTTP tools.",
        "description": "Greets the caller, confirms their name and number, collects the booking with an on-screen form, checks availability and books through two HTTP tools you point at your own booking system, logs the booking in a table and confirms. Set up for a fictional practice, Acme Dental.",
        "category": "scheduling",
        "chips": [
          "flow",
          "variables",
          "forms",
          "table",
          "http_tools"
        ],
        "requires": {
          "provider_keys": [],
          "telephony": false,
          "webhook_endpoint": false,
          "storage": false
        },
        "order": 20,
        "pack_id": "generic",
        "instructions": "You are the receptionist of Acme Dental, a fictional dental practice. You answer the phone and the web call page, and you book, move or cancel appointments.\n\n- Be brief and warm. One question at a time.\n- Use search_knowledge for opening hours, the address, parking, services and the cancellation policy.\n- Never invent availability or confirmation numbers: check_availability and book_appointment are the only source of truth.\n- If a tool fails, say \"I couldn't reach the booking system\" and offer to take the caller's name and number with push_note so the practice can call back.\n- If the caller asks for a person, call escalate_to_human.\n\nThis agent runs as a flow: each step's own instructions say what to do next.",
        "builtin_tools_disabled": [
          "pin_frame",
          "describe_current_frame"
        ],
        "default_voice": {},
        "panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "booking",
              "type": "form",
              "title": "Booking details",
              "config": {},
              "order": 1
            },
            {
              "id": "bookings",
              "type": "table",
              "title": "Bookings",
              "config": {
                "columns": [
                  {
                    "key": "name",
                    "label": "Name"
                  },
                  {
                    "key": "phone",
                    "label": "Phone"
                  },
                  {
                    "key": "service",
                    "label": "Service"
                  },
                  {
                    "key": "time",
                    "label": "Time"
                  }
                ]
              },
              "order": 2
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Notes",
              "config": {},
              "order": 3
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 4
            }
          ]
        },
        "voice": {
          "user_away_timeout_s": 20.0
        },
        "http_request_enabled": false,
        "tool_seeds": [
          {
            "definition": {
              "kind": "http",
              "name": "check_availability",
              "description": "Check free slots for a service on a date; returns the nearest alternatives when the slot is taken",
              "parameters": {
                "type": "object",
                "properties": {
                  "service": {
                    "type": "string",
                    "description": "cleaning, check-up, filling or consultation"
                  },
                  "date": {
                    "type": "string",
                    "description": "The requested date, YYYY-MM-DD"
                  }
                },
                "required": [
                  "service",
                  "date"
                ]
              },
              "method": "GET",
              "url": "https://example.com/appointments/availability?service={{ service }}&date={{ date }}",
              "headers": {},
              "allowed_hosts": [
                "example.com"
              ],
              "timeout_s": 10.0,
              "max_result_chars": 2000,
              "silent_reply": false
            },
            "enabled": true
          },
          {
            "definition": {
              "kind": "http",
              "name": "book_appointment",
              "description": "Book the appointment; returns the confirmation id",
              "parameters": {
                "type": "object",
                "properties": {
                  "name": {
                    "type": "string",
                    "description": "The caller's full name"
                  },
                  "phone": {
                    "type": "string",
                    "description": "The caller's phone number"
                  },
                  "service": {
                    "type": "string",
                    "description": "The service to book"
                  },
                  "time": {
                    "type": "string",
                    "description": "The confirmed slot, ISO 8601"
                  }
                },
                "required": [
                  "name",
                  "phone",
                  "service",
                  "time"
                ]
              },
              "method": "POST",
              "url": "https://example.com/appointments",
              "headers": {
                "Content-Type": "application/json"
              },
              "body_template": "{\"name\": \"{{ name }}\", \"phone\": \"{{ phone }}\", \"service\": \"{{ service }}\", \"time\": \"{{ time }}\"}",
              "allowed_hosts": [
                "example.com"
              ],
              "timeout_s": 10.0,
              "max_result_chars": 2000,
              "silent_reply": false
            },
            "enabled": true
          }
        ],
        "kb_seeds": [
          {
            "kb_name": "Receptionist · Practice info",
            "files": [
              "practice_info.md"
            ]
          }
        ],
        "flow": {
          "v": 1,
          "nodes": [
            {
              "id": "start",
              "kind": "start",
              "label": "Start",
              "position": [
                0.0,
                0.0
              ],
              "greeting": "Thanks for calling Acme Dental. I can book, move or cancel an appointment — how can I help?",
              "greeting_mode": "say"
            },
            {
              "id": "global",
              "kind": "global",
              "label": "Global",
              "position": [
                0.0,
                140.0
              ],
              "instructions": "You are the receptionist of Acme Dental. Be brief and warm. Use search_knowledge for opening hours, location and services. If the caller asks for a person, call escalate_to_human. Never invent availability: use the tools.",
              "tools": [
                "search_knowledge",
                "escalate_to_human",
                "current_time",
                "push_note"
              ],
              "kb_ids": []
            },
            {
              "id": "identify",
              "kind": "agent",
              "label": "Identify the caller",
              "position": [
                0.0,
                280.0
              ],
              "instructions": "Get the caller's full name and phone number; confirm the phone number back digit by digit.",
              "tools": [],
              "kb_ids": [],
              "extract": [
                "caller_name",
                "phone"
              ],
              "providers": {}
            },
            {
              "id": "collect_booking",
              "kind": "agent",
              "label": "Collect the booking",
              "position": [
                0.0,
                420.0
              ],
              "instructions": "Ask which service they need and when they would like to come in. Then call request_form with fields service (enum: cleaning, check-up, filling, consultation), preferred_date (date), preferred_time (string) so the caller can confirm the details on screen.",
              "tools": [
                "request_form",
                "current_time"
              ],
              "kb_ids": [],
              "extract": [
                "service",
                "preferred_time"
              ],
              "providers": {}
            },
            {
              "id": "book",
              "kind": "agent",
              "label": "Book",
              "position": [
                0.0,
                560.0
              ],
              "instructions": "Call check_availability with the requested slot. If free, call book_appointment, then table_append one row to the Bookings table and read the confirmation back. If not free, offer the two nearest alternatives the tool returned.",
              "tools": [
                "check_availability",
                "book_appointment",
                "table_append",
                "set_status"
              ],
              "kb_ids": [],
              "extract": [],
              "providers": {}
            },
            {
              "id": "done",
              "kind": "end",
              "label": "Booked",
              "position": [
                0.0,
                700.0
              ],
              "farewell": "You're booked. We'll text a reminder the day before. Goodbye!",
              "disposition": "booked",
              "webhook_event": true
            },
            {
              "id": "no_booking",
              "kind": "end",
              "label": "Not booked",
              "position": [
                0.0,
                840.0
              ],
              "farewell": "No problem — call back any time and we'll find a slot. Goodbye!",
              "disposition": "not_booked",
              "webhook_event": true
            }
          ],
          "edges": [
            {
              "id": "start_identify",
              "source": "start",
              "target": "identify",
              "condition": "the caller wants to book, move or cancel",
              "priority": 0
            },
            {
              "id": "identify_collect",
              "source": "identify",
              "target": "collect_booking",
              "condition": "name and phone are confirmed",
              "priority": 0
            },
            {
              "id": "collect_book",
              "source": "collect_booking",
              "target": "book",
              "condition": "the form was submitted or the service and time are confirmed verbally",
              "priority": 0
            },
            {
              "id": "book_done",
              "source": "book",
              "target": "done",
              "condition": "the booking is confirmed",
              "priority": 0
            },
            {
              "id": "book_no_booking",
              "source": "book",
              "target": "no_booking",
              "condition": "no slot works for the caller",
              "priority": 0
            },
            {
              "id": "identify_no_booking",
              "source": "identify",
              "target": "no_booking",
              "condition": "the caller does not want to book",
              "priority": 0
            }
          ],
          "variables": [
            {
              "name": "caller_name",
              "type": "string",
              "description": "The caller's full name",
              "required": true
            },
            {
              "name": "phone",
              "type": "phone",
              "description": "The caller's phone number",
              "required": true
            },
            {
              "name": "service",
              "type": "enum",
              "description": "The service to book",
              "options": [
                "cleaning",
                "check-up",
                "filling",
                "consultation"
              ],
              "required": false
            },
            {
              "name": "preferred_time",
              "type": "string",
              "description": "When the caller would like to come in",
              "required": false
            }
          ]
        },
        "pack_settings": {},
        "sample_prompts": [
          "I'd like to book a cleaning next Tuesday morning.",
          "What are your opening hours?",
          "Can I cancel my appointment?"
        ],
        "next_steps": [
          {
            "label": "Point check_availability and book_appointment at your booking system",
            "section": "tools"
          },
          {
            "label": "Replace the practice info",
            "section": "knowledge"
          },
          {
            "label": "Review the flow",
            "section": "flow"
          },
          {
            "label": "Make a test call",
            "section": "providers"
          }
        ]
      },
      "pack": {
        "v": 1,
        "id": "generic",
        "version": "0.1.0",
        "name": "Generic assistant",
        "description": "A plain voice assistant with no pack-specific tools or UI state.",
        "ui_panel_id": "generic",
        "default_panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Notes",
              "config": {},
              "order": 1
            },
            {
              "id": "checklist",
              "type": "checklist",
              "title": "Still needed",
              "config": {},
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "blocks": [],
        "default_instructions": "You are a helpful voice assistant. Answer questions clearly and briefly, and ask a clarifying question when the user's request is ambiguous.",
        "default_greeting": "Hello! How can I help you today?",
        "default_voice": {},
        "recommended_pipeline": {
          "mode": "cascaded",
          "stt": {
            "provider_id": "livekit-inference-stt",
            "model": "deepgram/nova-3",
            "fields": {}
          },
          "llm": {
            "provider_id": "livekit-inference-llm",
            "model": "google/gemma-4-31b-it",
            "fields": {}
          },
          "tts": {
            "provider_id": "livekit-inference-tts",
            "model": "inworld/inworld-tts-2",
            "fields": {}
          },
          "avatar_options": {
            "participant_name": "Avatar"
          },
          "turn_handling": {}
        },
        "capabilities": {
          "camera": false,
          "screen_share": false,
          "chat_input": true,
          "vision_inject_per_turn": true,
          "dtmf": false
        },
        "builtin_tools_disabled": [],
        "tool_names": [],
        "state_schema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        },
        "settings_schema": {
          "type": "object"
        },
        "kb_seeds": [],
        "instructions_by_mode": {}
      },
      "derived": false
    },
    {
      "template": {
        "v": 1,
        "id": "vision_assistant",
        "name": "Vision assistant",
        "tagline": "Sees your camera or shared screen, reads it aloud and pins what matters.",
        "description": "A multimodal assistant: describes what the camera shows, reads a shared screen, pins frames to a gallery with a caption and keeps notes. Runs on LiveKit Inference with a vision-capable model, so it needs no vendor key.",
        "category": "vision",
        "chips": [
          "camera",
          "screen_share",
          "gallery"
        ],
        "requires": {
          "provider_keys": [],
          "telephony": false,
          "webhook_endpoint": false,
          "storage": false
        },
        "order": 30,
        "pack_id": "generic",
        "instructions": "You are a visual assistant. The caller can turn on their camera or share their screen, and you describe what you see and help with it.\n\n- When the caller mentions something visual and neither the camera nor a screen share is on, ask them to turn on the camera or share the screen.\n- Describe concretely: objects, text, numbers, labels, colours and the state of things (on or off, open or closed, an error or a warning). Keep each description to two or three sentences unless asked for more.\n- Read text aloud exactly when asked, including error messages, model numbers and serial numbers.\n- Call pin_frame with a short caption when the caller says \"keep this\", \"save this\" or \"pin this\", and when something matters: an error message, a serial number, a meter reading, damage.\n- Call push_note for facts worth remembering later in the call (a serial number, a reading, a step the caller already tried).\n- Call describe_current_frame when you need a closer look before answering.\n- Never claim to see something that is not in the frame. If the image is blurry, dark or cut off, say so and ask the caller to move closer or adjust the light.\n- Call set_status with a short word for what you are doing, for example \"looking\" or \"reading\".",
        "greeting": "Hi! Turn on your camera or share your screen and I'll tell you what I see — or just ask.",
        "pipeline": {
          "mode": "cascaded",
          "stt": {
            "provider_id": "livekit-inference-stt",
            "model": "deepgram/nova-3",
            "fields": {}
          },
          "llm": {
            "provider_id": "livekit-inference-llm",
            "model": "google/gemini-3.5-flash",
            "fields": {}
          },
          "tts": {
            "provider_id": "livekit-inference-tts",
            "model": "inworld/inworld-tts-2",
            "fields": {}
          },
          "avatar_options": {
            "participant_name": "Avatar"
          },
          "turn_handling": {}
        },
        "capabilities": {
          "camera": true,
          "screen_share": true,
          "chat_input": true,
          "vision_inject_per_turn": true,
          "dtmf": false
        },
        "builtin_tools_disabled": [],
        "default_voice": {},
        "panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "frames",
              "type": "gallery",
              "title": "Pinned frames",
              "config": {},
              "order": 1
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Observations",
              "config": {},
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "http_request_enabled": false,
        "tool_seeds": [],
        "kb_seeds": [],
        "pack_settings": {},
        "sample_prompts": [
          "What's on my screen right now?",
          "Read the error message to me.",
          "Pin this and note the serial number."
        ],
        "next_steps": [
          {
            "label": "Optional: add a Google key and switch to Gemini Live for realtime video",
            "section": "providers"
          },
          {
            "label": "Tune what it should look for",
            "section": "instructions"
          },
          {
            "label": "Make a test call with the camera on",
            "section": "providers"
          }
        ]
      },
      "pack": {
        "v": 1,
        "id": "generic",
        "version": "0.1.0",
        "name": "Generic assistant",
        "description": "A plain voice assistant with no pack-specific tools or UI state.",
        "ui_panel_id": "generic",
        "default_panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Notes",
              "config": {},
              "order": 1
            },
            {
              "id": "checklist",
              "type": "checklist",
              "title": "Still needed",
              "config": {},
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "blocks": [],
        "default_instructions": "You are a helpful voice assistant. Answer questions clearly and briefly, and ask a clarifying question when the user's request is ambiguous.",
        "default_greeting": "Hello! How can I help you today?",
        "default_voice": {},
        "recommended_pipeline": {
          "mode": "cascaded",
          "stt": {
            "provider_id": "livekit-inference-stt",
            "model": "deepgram/nova-3",
            "fields": {}
          },
          "llm": {
            "provider_id": "livekit-inference-llm",
            "model": "google/gemma-4-31b-it",
            "fields": {}
          },
          "tts": {
            "provider_id": "livekit-inference-tts",
            "model": "inworld/inworld-tts-2",
            "fields": {}
          },
          "avatar_options": {
            "participant_name": "Avatar"
          },
          "turn_handling": {}
        },
        "capabilities": {
          "camera": false,
          "screen_share": false,
          "chat_input": true,
          "vision_inject_per_turn": true,
          "dtmf": false
        },
        "builtin_tools_disabled": [],
        "tool_names": [],
        "state_schema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        },
        "settings_schema": {
          "type": "object"
        },
        "kb_seeds": [],
        "instructions_by_mode": {}
      },
      "derived": false
    },
    {
      "template": {
        "v": 1,
        "id": "phone_agent",
        "name": "Phone agent",
        "tagline": "Answers a phone number with a keypad menu, takes messages and scores every call.",
        "description": "A telephony starter: a keypad menu, answers from a small FAQ, takes a message, transfers to a person once you add a destination, and scores every call with QA. Attach a number and a dispatch rule to put it on the phone; until then, try it in the browser or a text chat.",
        "category": "phone",
        "chips": [
          "telephony",
          "dtmf",
          "transfer",
          "qa"
        ],
        "requires": {
          "provider_keys": [],
          "telephony": true,
          "webhook_endpoint": false,
          "storage": false
        },
        "order": 40,
        "pack_id": "generic",
        "instructions": "You answer the phone for Acme, a fictional small business. Callers hear you; they cannot see a screen.\n\nPhone etiquette:\n- Short sentences. One question at a time.\n- Confirm names and numbers by reading them back, digit by digit for numbers.\n- Never read out a list longer than three items; offer the rest.\n\nKeypad menu:\n- Keypad presses arrive as user turns such as \"[The caller pressed 1]\".\n- 1: opening hours. Use search_knowledge and answer briefly.\n- 2: leave a message. Ask for the caller's name, number and message, read them back, then call push_note with all three and set_status \"message_taken\".\n- 0: a person. See Transfers below.\n- If the caller speaks instead of pressing a key, just help them.\n\nAnswers:\n- Use search_knowledge for opening hours, the address, prices and common questions. If the answer is not there, offer to take a message.\n\nTransfers:\n- Call transfer_call only when the caller asks for a person and a transfer destination exists. Tell the caller you are putting them through, then call set_status \"transferred\".\n- If no transfer destination is set up, apologise and offer to take a message instead.\n\nEnding:\n- When the caller is done, thank them, say goodbye and call end_call.",
        "greeting": "Thanks for calling Acme. Press 1 for opening hours, 2 to leave a message, or just tell me what you need.",
        "capabilities": {
          "camera": false,
          "screen_share": false,
          "chat_input": true,
          "vision_inject_per_turn": true,
          "dtmf": true
        },
        "builtin_tools_disabled": [
          "pin_frame",
          "describe_current_frame"
        ],
        "default_voice": {},
        "panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "messages",
              "type": "notes",
              "title": "Messages",
              "config": {},
              "order": 1
            },
            {
              "id": "transcript",
              "type": "transcript",
              "title": "Transcript",
              "config": {
                "show_tools": false
              },
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "voice": {
          "allow_interruptions": true,
          "user_away_timeout_s": 20.0
        },
        "http_request_enabled": false,
        "tool_seeds": [],
        "kb_seeds": [
          {
            "kb_name": "Phone agent · FAQ",
            "files": [
              "phone_faq.md"
            ]
          }
        ],
        "qa": {
          "enabled": true,
          "rubric_prompt": "Score the call from 1 to 5 on each of: the greeting was given; the caller's need was identified; the need was resolved or routed (message taken or transfer offered); the tone was polite and concise; the call was closed properly. Give one sentence of evidence per item."
        },
        "telephony": {
          "transfer_targets": []
        },
        "pack_settings": {},
        "sample_prompts": [
          "What time do you close today?",
          "I want to leave a message for the manager.",
          "Can I speak to someone?"
        ],
        "next_steps": [
          {
            "label": "Attach a number and a dispatch rule",
            "href": "/console/telephony"
          },
          {
            "label": "Add a transfer destination",
            "href": "/console/telephony"
          },
          {
            "label": "Add a storage config to record calls",
            "section": "recording"
          },
          {
            "label": "Test it in a text chat before calling",
            "section": "providers"
          }
        ]
      },
      "pack": {
        "v": 1,
        "id": "generic",
        "version": "0.1.0",
        "name": "Generic assistant",
        "description": "A plain voice assistant with no pack-specific tools or UI state.",
        "ui_panel_id": "generic",
        "default_panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Notes",
              "config": {},
              "order": 1
            },
            {
              "id": "checklist",
              "type": "checklist",
              "title": "Still needed",
              "config": {},
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "blocks": [],
        "default_instructions": "You are a helpful voice assistant. Answer questions clearly and briefly, and ask a clarifying question when the user's request is ambiguous.",
        "default_greeting": "Hello! How can I help you today?",
        "default_voice": {},
        "recommended_pipeline": {
          "mode": "cascaded",
          "stt": {
            "provider_id": "livekit-inference-stt",
            "model": "deepgram/nova-3",
            "fields": {}
          },
          "llm": {
            "provider_id": "livekit-inference-llm",
            "model": "google/gemma-4-31b-it",
            "fields": {}
          },
          "tts": {
            "provider_id": "livekit-inference-tts",
            "model": "inworld/inworld-tts-2",
            "fields": {}
          },
          "avatar_options": {
            "participant_name": "Avatar"
          },
          "turn_handling": {}
        },
        "capabilities": {
          "camera": false,
          "screen_share": false,
          "chat_input": true,
          "vision_inject_per_turn": true,
          "dtmf": false
        },
        "builtin_tools_disabled": [],
        "tool_names": [],
        "state_schema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        },
        "settings_schema": {
          "type": "object"
        },
        "kb_seeds": [],
        "instructions_by_mode": {}
      },
      "derived": false
    },
    {
      "template": {
        "v": 1,
        "id": "lead_qualification",
        "name": "Lead qualification",
        "tagline": "Qualifies inbound leads with a flow, routes them and posts the result to your CRM.",
        "description": "Asks a fixed set of qualification questions, extracts the answers as flow variables, routes the lead to a demo or to nurture, and scores the call. The session.ended webhook carries the variables and the disposition to your CRM. Set up for a fictional product, Acme.",
        "category": "sales",
        "chips": [
          "flow",
          "variables",
          "webhook",
          "qa",
          "rag"
        ],
        "requires": {
          "provider_keys": [],
          "telephony": false,
          "webhook_endpoint": true,
          "storage": false
        },
        "order": 50,
        "pack_id": "generic",
        "instructions": "You qualify inbound leads for Acme, a fictional software company. Your job is to learn enough about the caller that the right person can follow up.\n\n- One question at a time. Acknowledge each answer briefly before the next question.\n- Be consultative, never pushy. \"Not sure\" is a fine answer.\n- Use search_knowledge for questions about plans, pricing bands and integrations; answer from the offer sheet only.\n- Call push_note for anything unusual (a competitor mentioned, a hard deadline, a special requirement).\n\nThis agent runs as a flow: each step's own instructions say what to ask, and the answers are saved as flow variables for the session.ended webhook.",
        "builtin_tools_disabled": [
          "pin_frame",
          "describe_current_frame"
        ],
        "default_voice": {},
        "panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Qualification notes",
              "config": {},
              "order": 1
            },
            {
              "id": "offer",
              "type": "kb_citations",
              "title": "Offer details",
              "config": {},
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "http_request_enabled": false,
        "tool_seeds": [],
        "kb_seeds": [
          {
            "kb_name": "Lead qualification · Offer sheet",
            "files": [
              "offer_sheet.md"
            ]
          }
        ],
        "flow": {
          "v": 1,
          "nodes": [
            {
              "id": "start",
              "kind": "start",
              "label": "Start",
              "position": [
                0.0,
                0.0
              ],
              "greeting": "Hi, thanks for your interest in Acme. I'll ask a few quick questions so the right person can follow up. Sound good?",
              "greeting_mode": "say"
            },
            {
              "id": "global",
              "kind": "global",
              "label": "Global",
              "position": [
                0.0,
                140.0
              ],
              "instructions": "You qualify leads for Acme. One question at a time, acknowledge each answer, never pressure. Use search_knowledge when asked about pricing or features. push_note anything unusual.",
              "tools": [
                "search_knowledge",
                "push_note",
                "set_status"
              ],
              "kb_ids": []
            },
            {
              "id": "company",
              "kind": "agent",
              "label": "Company",
              "position": [
                0.0,
                280.0
              ],
              "instructions": "Ask for the company name and the caller's role.",
              "tools": [],
              "kb_ids": [],
              "extract": [
                "company",
                "role"
              ],
              "providers": {}
            },
            {
              "id": "needs",
              "kind": "agent",
              "label": "Needs",
              "position": [
                0.0,
                420.0
              ],
              "instructions": "Ask what they are trying to solve, how many people would use it, and when they want to start.",
              "tools": [],
              "kb_ids": [],
              "extract": [
                "use_case",
                "team_size",
                "timeline"
              ],
              "providers": {}
            },
            {
              "id": "budget",
              "kind": "agent",
              "label": "Budget",
              "position": [
                0.0,
                560.0
              ],
              "instructions": "Ask whether they have a budget range in mind; accept 'not sure'.",
              "tools": [],
              "kb_ids": [],
              "extract": [
                "budget_range"
              ],
              "providers": {}
            },
            {
              "id": "book_demo",
              "kind": "agent",
              "label": "Book a demo",
              "position": [
                0.0,
                700.0
              ],
              "instructions": "Offer a demo with a specialist; ask for the best email and a preferred day. Call set_status with qualified.",
              "tools": [],
              "kb_ids": [],
              "extract": [
                "email"
              ],
              "providers": {}
            },
            {
              "id": "qualified",
              "kind": "end",
              "label": "Qualified",
              "position": [
                0.0,
                840.0
              ],
              "farewell": "Perfect — a specialist will email you within one business day to confirm the demo.",
              "disposition": "qualified",
              "webhook_event": true
            },
            {
              "id": "nurture",
              "kind": "end",
              "label": "Nurture",
              "position": [
                0.0,
                980.0
              ],
              "farewell": "Thanks — I'll send over some material and we can pick this up when the timing is right.",
              "disposition": "nurture",
              "webhook_event": true
            },
            {
              "id": "qa",
              "kind": "qa",
              "label": "QA",
              "position": [
                0.0,
                1120.0
              ],
              "rubric_prompt": "Score 1–5: were all qualification questions asked, was the routing decision consistent with the answers, was the tone consultative, did the agent avoid pressure."
            }
          ],
          "edges": [
            {
              "id": "start_company",
              "source": "start",
              "target": "company",
              "condition": "the caller agrees",
              "priority": 0
            },
            {
              "id": "company_needs",
              "source": "company",
              "target": "needs",
              "condition": "the company name and role are known",
              "priority": 0
            },
            {
              "id": "needs_budget",
              "source": "needs",
              "target": "budget",
              "condition": "the use case, team size and timeline are known",
              "priority": 0
            },
            {
              "id": "budget_book_demo",
              "source": "budget",
              "target": "book_demo",
              "condition": "timeline is within 6 months and team_size is 5 or more",
              "priority": 0
            },
            {
              "id": "budget_nurture",
              "source": "budget",
              "target": "nurture",
              "condition": "timeline is later than 6 months, or team_size is under 5, or the caller is only researching",
              "priority": 0
            },
            {
              "id": "book_demo_qualified",
              "source": "book_demo",
              "target": "qualified",
              "condition": "email collected",
              "priority": 0
            },
            {
              "id": "start_nurture",
              "source": "start",
              "target": "nurture",
              "condition": "the caller declines to answer questions",
              "priority": 0
            }
          ],
          "variables": [
            {
              "name": "company",
              "type": "string",
              "description": "The caller's company",
              "required": true
            },
            {
              "name": "role",
              "type": "string",
              "description": "The caller's role",
              "required": false
            },
            {
              "name": "use_case",
              "type": "string",
              "description": "What they are trying to solve",
              "required": false
            },
            {
              "name": "team_size",
              "type": "number",
              "description": "How many people would use it",
              "required": false
            },
            {
              "name": "timeline",
              "type": "enum",
              "description": "When they want to start",
              "options": [
                "now",
                "1_3_months",
                "3_6_months",
                "later"
              ],
              "required": false
            },
            {
              "name": "budget_range",
              "type": "enum",
              "description": "Their budget range",
              "options": [
                "under_1k",
                "1k_10k",
                "over_10k",
                "not_sure"
              ],
              "required": false
            },
            {
              "name": "email",
              "type": "email",
              "description": "The best email for the follow-up",
              "required": false
            }
          ]
        },
        "pack_settings": {},
        "sample_prompts": [
          "We're a 20-person team looking at this for Q4.",
          "How much does it cost?",
          "I'm just researching for now."
        ],
        "next_steps": [
          {
            "label": "Add a webhook for session.ended to receive the variables and disposition",
            "href": "/console/settings?tab=webhooks"
          },
          {
            "label": "Edit the questions in the flow",
            "section": "flow"
          },
          {
            "label": "Replace the offer sheet",
            "section": "knowledge"
          },
          {
            "label": "Make a test call",
            "section": "providers"
          }
        ]
      },
      "pack": {
        "v": 1,
        "id": "generic",
        "version": "0.1.0",
        "name": "Generic assistant",
        "description": "A plain voice assistant with no pack-specific tools or UI state.",
        "ui_panel_id": "generic",
        "default_panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Notes",
              "config": {},
              "order": 1
            },
            {
              "id": "checklist",
              "type": "checklist",
              "title": "Still needed",
              "config": {},
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "blocks": [],
        "default_instructions": "You are a helpful voice assistant. Answer questions clearly and briefly, and ask a clarifying question when the user's request is ambiguous.",
        "default_greeting": "Hello! How can I help you today?",
        "default_voice": {},
        "recommended_pipeline": {
          "mode": "cascaded",
          "stt": {
            "provider_id": "livekit-inference-stt",
            "model": "deepgram/nova-3",
            "fields": {}
          },
          "llm": {
            "provider_id": "livekit-inference-llm",
            "model": "google/gemma-4-31b-it",
            "fields": {}
          },
          "tts": {
            "provider_id": "livekit-inference-tts",
            "model": "inworld/inworld-tts-2",
            "fields": {}
          },
          "avatar_options": {
            "participant_name": "Avatar"
          },
          "turn_handling": {}
        },
        "capabilities": {
          "camera": false,
          "screen_share": false,
          "chat_input": true,
          "vision_inject_per_turn": true,
          "dtmf": false
        },
        "builtin_tools_disabled": [],
        "tool_names": [],
        "state_schema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        },
        "settings_schema": {
          "type": "object"
        },
        "kb_seeds": [],
        "instructions_by_mode": {}
      },
      "derived": false
    },
    {
      "template": {
        "v": 1,
        "id": "survey_intake",
        "name": "Survey / intake form",
        "tagline": "Runs a short questionnaire by voice and confirms the answers on an on-screen form.",
        "description": "Runs a fixed five-question feedback survey by voice, shows the answers as an on-screen form the respondent confirms, and appends one row per completed survey to a table. A prompt agent, so the questions live in the instructions.",
        "category": "forms",
        "chips": [
          "forms",
          "table"
        ],
        "requires": {
          "provider_keys": [],
          "telephony": false,
          "webhook_endpoint": false,
          "storage": false
        },
        "order": 60,
        "pack_id": "generic",
        "instructions": "You run a short customer feedback survey by voice. There are five questions; ask them in this order, one at a time:\n\n1. What is your name?\n2. On a scale of 1 to 5, how satisfied are you with our service overall?\n3. Would you recommend us to a friend or colleague? Yes or no.\n4. What is one thing we should improve?\n5. Is there anything else you'd like to tell us?\n\nHow to run it:\n- Keep it friendly and quick. Acknowledge each answer in a few words.\n- Accept partial answers (\"somewhere around four\" is a 4). If the respondent skips a question, move on.\n- Call set_status with \"in_progress\" when you start.\n- After the last question, call request_form with the fields name, satisfaction (number 1 to 5), recommend (yes or no) and comments, prefilled with the answers, so the respondent can correct them on screen.\n- When the form comes back, call table_append with one row for the Responses table (name, satisfaction, recommend, comments), then set_status \"completed\".\n- Thank the respondent, say goodbye and call end_call.",
        "greeting": "Hi! This is a two-minute feedback survey — five quick questions. Ready?",
        "builtin_tools_disabled": [
          "pin_frame",
          "describe_current_frame",
          "escalate_to_human"
        ],
        "default_voice": {},
        "panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "answers",
              "type": "form",
              "title": "Your answers",
              "config": {},
              "order": 1
            },
            {
              "id": "responses",
              "type": "table",
              "title": "Responses",
              "config": {
                "columns": [
                  {
                    "key": "name",
                    "label": "Name"
                  },
                  {
                    "key": "satisfaction",
                    "label": "Satisfaction"
                  },
                  {
                    "key": "recommend",
                    "label": "Recommend"
                  },
                  {
                    "key": "comments",
                    "label": "Comments"
                  }
                ]
              },
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "voice": {
          "user_away_timeout_s": 25.0
        },
        "http_request_enabled": false,
        "tool_seeds": [],
        "kb_seeds": [],
        "pack_settings": {},
        "sample_prompts": [
          "Sure, let's go.",
          "I'd say four out of five.",
          "The onboarding took too long."
        ],
        "next_steps": [
          {
            "label": "Change the questions",
            "section": "instructions"
          },
          {
            "label": "Adjust the response table's columns",
            "section": "panel"
          },
          {
            "label": "Make a test call",
            "section": "providers"
          }
        ]
      },
      "pack": {
        "v": 1,
        "id": "generic",
        "version": "0.1.0",
        "name": "Generic assistant",
        "description": "A plain voice assistant with no pack-specific tools or UI state.",
        "ui_panel_id": "generic",
        "default_panel": {
          "panel_id": "composite",
          "layout": "side",
          "blocks": [
            {
              "id": "status",
              "type": "status",
              "config": {},
              "order": 0
            },
            {
              "id": "notes",
              "type": "notes",
              "title": "Notes",
              "config": {},
              "order": 1
            },
            {
              "id": "checklist",
              "type": "checklist",
              "title": "Still needed",
              "config": {},
              "order": 2
            },
            {
              "id": "activity",
              "type": "activity",
              "title": "Activity",
              "config": {},
              "order": 3
            }
          ]
        },
        "blocks": [],
        "default_instructions": "You are a helpful voice assistant. Answer questions clearly and briefly, and ask a clarifying question when the user's request is ambiguous.",
        "default_greeting": "Hello! How can I help you today?",
        "default_voice": {},
        "recommended_pipeline": {
          "mode": "cascaded",
          "stt": {
            "provider_id": "livekit-inference-stt",
            "model": "deepgram/nova-3",
            "fields": {}
          },
          "llm": {
            "provider_id": "livekit-inference-llm",
            "model": "google/gemma-4-31b-it",
            "fields": {}
          },
          "tts": {
            "provider_id": "livekit-inference-tts",
            "model": "inworld/inworld-tts-2",
            "fields": {}
          },
          "avatar_options": {
            "participant_name": "Avatar"
          },
          "turn_handling": {}
        },
        "capabilities": {
          "camera": false,
          "screen_share": false,
          "chat_input": true,
          "vision_inject_per_turn": true,
          "dtmf": false
        },
        "builtin_tools_disabled": [],
        "tool_names": [],
        "state_schema": {
          "type": "object",
          "properties": {},
          "additionalProperties": true
        },
        "settings_schema": {
          "type": "object"
        },
        "kb_seeds": [],
        "instructions_by_mode": {}
      },
      "derived": false
    },
    {
      "template": {
        "v": 1,
        "id": "insurance_claim",
        "name": "Insurance claim intake",
        "tagline": "The advanced example: a full code pack for first-notice-of-loss claim intake.",
        "description": "The full insurance code pack: first-notice-of-loss intake with policy lookup, claim extraction and classification, a document checklist, camera evidence and an incident sketch in a custom notebook panel, plus two seeded knowledge bases. An example of what a code pack adds on top of configuration.",
        "category": "example",
        "chips": [
          "code_tools",
          "knowledge_seeds",
          "camera",
          "image_gen"
        ],
        "requires": {
          "provider_keys": [
            {
              "provider_id": "google-image-gen",
              "optional": true,
              "purpose": "incident sketches"
            }
          ],
          "telephony": false,
          "webhook_endpoint": false,
          "storage": false
        },
        "order": 900,
        "pack_id": "insurance_claim",
        "default_voice": {},
        "http_request_enabled": false,
        "tool_seeds": [],
        "kb_seeds": [],
        "pack_settings": {},
        "sample_prompts": [
          "I had a small kitchen fire last night.",
          "My policy number is H0-44721.",
          "Can I show you the damage on camera?"
        ],
        "next_steps": [
          {
            "label": "Optional: add a Google key for incident sketches",
            "section": "providers"
          },
          {
            "label": "Read the pack's instructions",
            "section": "instructions"
          },
          {
            "label": "Make a test call with the camera on",
            "section": "providers"
          }
        ]
      },
      "pack": {
        "v": 1,
        "id": "insurance_claim",
        "version": "0.1.0",
        "name": "Insurance claim intake",
        "description": "A live voice intake agent for a first notice of loss (FNOL) team: verifies the policy, extracts and classifies the claim, tracks a document checklist, tapes camera evidence and an incident sketch into a shared notebook, and escalates safety concerns.",
        "ui_panel_id": "insurance_notebook",
        "default_panel": {
          "panel_id": "insurance_notebook",
          "layout": "side",
          "blocks": []
        },
        "blocks": [],
        "default_instructions": "You are the live voice intake agent for an insurance first notice of loss (FNOL) team.\nSpeak naturally, warmly, and briefly. The claimant may be stressed. Acknowledge what\nthey say, then keep the intake moving one or two questions at a time. Your notes are\nwritten into a field notebook the claimant can see, so narrate what you are doing in\nshort asides like \"I'm noting that\" or \"let me check that policy\".\n\nYou work with a claim team that runs in the background while you talk:\n- lookup_policy: verifies a policy number against the carrier's policy records.\n  Call it as soon as you hear a policy number. Keep talking while it runs. When it\n  returns, confirm the policyholder name and policy line out loud in one sentence.\n  If the policy is lapsed or not found, say a human reviewer will check it and do\n  not stop collecting loss facts.\n- sync_claim_packet: sends everything the claimant has said, plus your camera\n  observations, to the claim team, which extracts facts, applies deterministic\n  intake rules, and returns the routing decision and the list of open items the\n  packet still needs. Call it after the claimant shares new loss facts, roughly\n  every turn or two. The open items are a checklist, not a script. Never interrupt\n  the current topic to ask one. Finish what the claimant is describing or showing,\n  acknowledge it, and only then pick the open item that follows naturally from\n  where the conversation is. Dates, addresses, and policy numbers wait until the\n  current topic is closed.\n- pin_evidence_photo: the claimant can turn on their camera and show you the damage.\n  Whenever you can see anything relevant to the claim on camera (damage, water\n  lines, dents, broken items, the scene or layout, a floor plan, receipts, documents,\n  serial numbers), say what you see in one or two concrete sentences and call\n  pin_evidence_photo in that same turn. The current camera frame gets taped into the\n  notebook. Do this the first time you see each new thing; do not wait to be asked\n  and do not postpone it to a later turn. Skip frames that show nothing relevant,\n  like a blank wall or a face.\n  Your eyes are the adjuster's eyes, so report only what you can actually see. If\n  the claimant names something (a crack, a leak, mold, a dent) and the frame does\n  not clearly show it, do not repeat their word as if you saw it. Say what you do\n  see (\"I can see a dark mark about the size of a coin, but I can't make out a crack\n  from here\"), ask them politely to get closer, tilt the camera, or add light, and\n  pin the frame with confirmed set to false. Pin it again with confirmed true once\n  you can see it. Being honest about what is visible is more helpful to the claimant\n  than agreeing, because the adjuster will look at the same photo.\n- draw_incident_sketch: once you understand the scene, call this with a short\n  description of the layout and what happened, written for an illustrator. The team\n  draws a rough pen sketch into the notebook. When it returns, ask the claimant\n  whether the sketch looks right. If they correct it, call it again with the fix.\n  Call it in the same turn you first learn where it happened and what happened,\n  alongside sync_claim_packet; do not wait for every detail or for the claimant to\n  ask. A rough first sketch that gets corrected is better than a late one.\n\nSafety first: if the claimant mentions injury, unsafe housing, or immediate danger,\ntell them to contact emergency services if anyone is in danger, say that a human\nrepresentative will take over, and call sync_claim_packet so the team escalates.\n\nStay with the claimant. Talk about what they are talking about; when the camera is\non, the conversation is about what is on camera until you both move on. While a\ncamera view is still unconfirmed, your next question is about getting a better view\n(closer, more light, a different angle), not about the packet. Double check the\ndetails that matter (what is visible, dates, amounts, who was involved) and if\nsomething does not add up, ask about it politely instead of writing it down. Never\nannounce that you are checking a list or the packet; just ask the next question.\n\nNever promise coverage, payment, liability, benefits, or approval. Policy details\nfrom lookup_policy describe what is on the policy, not what will be paid.\nWhen the core facts and blocking items are collected, summarize the claim back in\ntwo sentences and tell the claimant an adjuster will be in touch.",
        "default_greeting": "I can start the claim while we talk. First, are you and everyone else in a safe place?",
        "default_voice": {
          "google-realtime": "Kore"
        },
        "recommended_pipeline": {
          "mode": "cascaded",
          "stt": {
            "provider_id": "livekit-inference-stt",
            "model": "deepgram/nova-3",
            "fields": {}
          },
          "llm": {
            "provider_id": "livekit-inference-llm",
            "model": "google/gemini-3.5-flash",
            "fields": {}
          },
          "tts": {
            "provider_id": "livekit-inference-tts",
            "model": "inworld/inworld-tts-2",
            "fields": {}
          },
          "avatar_options": {
            "participant_name": "Avatar"
          },
          "image_gen": {
            "provider_id": "google-image-gen",
            "model": "gemini-3.1-flash-image",
            "fields": {}
          },
          "turn_handling": {}
        },
        "capabilities": {
          "camera": true,
          "screen_share": false,
          "chat_input": true,
          "vision_inject_per_turn": true,
          "dtmf": false
        },
        "builtin_tools_disabled": [
          "pin_frame",
          "push_note",
          "set_status",
          "escalate_to_human",
          "http_request"
        ],
        "tool_names": [
          "lookup_policy",
          "sync_claim_packet",
          "pin_evidence_photo",
          "draw_incident_sketch"
        ],
        "state_schema": {
          "$defs": {
            "DocumentEntry": {
              "description": "A document checklist entry with full fidelity (reason, priority).",
              "properties": {
                "item": {
                  "title": "Item",
                  "type": "string"
                },
                "reason": {
                  "title": "Reason",
                  "type": "string"
                },
                "priority": {
                  "enum": [
                    "required",
                    "recommended",
                    "conditional"
                  ],
                  "title": "Priority",
                  "type": "string"
                },
                "already_provided": {
                  "default": false,
                  "title": "Already Provided",
                  "type": "boolean"
                }
              },
              "required": [
                "item",
                "reason",
                "priority"
              ],
              "title": "DocumentEntry",
              "type": "object"
            },
            "EventEntry": {
              "description": "One audit-log style event, was the old ``events`` list entries.",
              "properties": {
                "tone": {
                  "enum": [
                    "neutral",
                    "info",
                    "success",
                    "warning",
                    "danger"
                  ],
                  "title": "Tone",
                  "type": "string"
                },
                "title": {
                  "title": "Title",
                  "type": "string"
                },
                "detail": {
                  "title": "Detail",
                  "type": "string"
                },
                "rule": {
                  "title": "Rule",
                  "type": "string"
                }
              },
              "required": [
                "tone",
                "title",
                "detail",
                "rule"
              ],
              "title": "EventEntry",
              "type": "object"
            },
            "FieldEntry": {
              "description": "One notebook field: value, completeness status, and where it came from.",
              "properties": {
                "label": {
                  "title": "Label",
                  "type": "string"
                },
                "value": {
                  "title": "Value",
                  "type": "string"
                },
                "status": {
                  "enum": [
                    "missing",
                    "complete",
                    "urgent"
                  ],
                  "title": "Status",
                  "type": "string"
                },
                "source": {
                  "title": "Source",
                  "type": "string"
                }
              },
              "required": [
                "label",
                "value",
                "status",
                "source"
              ],
              "title": "FieldEntry",
              "type": "object"
            },
            "SketchState": {
              "description": "The current incident sketch, mirroring the old ``session.sketch`` dict.",
              "properties": {
                "asset_id": {
                  "title": "Asset Id",
                  "type": "string"
                },
                "brief": {
                  "title": "Brief",
                  "type": "string"
                },
                "version": {
                  "title": "Version",
                  "type": "integer"
                },
                "confirmed": {
                  "default": false,
                  "title": "Confirmed",
                  "type": "boolean"
                }
              },
              "required": [
                "asset_id",
                "brief",
                "version"
              ],
              "title": "SketchState",
              "type": "object"
            }
          },
          "description": "The pack's ``UiState.custom`` shape; ``manifest.state_schema`` is this model's JSON Schema.",
          "properties": {
            "fields": {
              "additionalProperties": {
                "$ref": "#/$defs/FieldEntry"
              },
              "title": "Fields",
              "type": "object"
            },
            "route": {
              "enum": [
                "ready_for_adjuster",
                "needs_docs",
                "special_investigation",
                "emergency_escalation"
              ],
              "title": "Route",
              "type": "string"
            },
            "missing_blockers": {
              "default": [],
              "items": {
                "type": "string"
              },
              "title": "Missing Blockers",
              "type": "array"
            },
            "documents": {
              "default": [],
              "items": {
                "$ref": "#/$defs/DocumentEntry"
              },
              "title": "Documents",
              "type": "array"
            },
            "handoff": {
              "additionalProperties": {
                "type": "string"
              },
              "title": "Handoff",
              "type": "object"
            },
            "packet_markdown": {
              "title": "Packet Markdown",
              "type": "string"
            },
            "events": {
              "default": [],
              "items": {
                "$ref": "#/$defs/EventEntry"
              },
              "title": "Events",
              "type": "array"
            },
            "policy": {
              "anyOf": [
                {
                  "additionalProperties": true,
                  "type": "object"
                },
                {
                  "type": "null"
                }
              ],
              "default": null,
              "title": "Policy"
            },
            "camera_notes": {
              "default": [],
              "items": {
                "type": "string"
              },
              "title": "Camera Notes",
              "type": "array"
            },
            "sketch": {
              "anyOf": [
                {
                  "$ref": "#/$defs/SketchState"
                },
                {
                  "type": "null"
                }
              ],
              "default": null
            },
            "severity": {
              "enum": [
                "low",
                "medium",
                "high",
                "urgent"
              ],
              "title": "Severity",
              "type": "string"
            },
            "claim_type": {
              "title": "Claim Type",
              "type": "string"
            }
          },
          "required": [
            "fields",
            "route",
            "handoff",
            "packet_markdown",
            "severity",
            "claim_type"
          ],
          "title": "InsuranceState",
          "type": "object"
        },
        "settings_schema": {
          "type": "object"
        },
        "kb_seeds": [
          {
            "kb_name": "Insurance policy lines",
            "files": [
              "policy_lines.md"
            ]
          },
          {
            "kb_name": "Intake playbook",
            "files": [
              "intake_playbook.md"
            ]
          }
        ],
        "instructions_by_mode": {
          "cascaded": "Pipeline note: you always say something out loud right after calling a background\ntool, even before it finishes. Keep that acknowledgement to one short clause, for\nexample \"Let me check that policy\" or \"Got it, updating the claim notes.\" Never\nread out the open items list, the document checklist, or the packet contents; the\nclaimant sees those written in the notebook. Only speak the tool's actual result\nonce it comes back, in the ordinary way described above."
        }
      },
      "derived": false
    }
  ]
};

/** One starter from the fixture by id (throws on a typo so a test fails loudly). */
export function templateById(id: string): TemplateOut {
  const item = TEMPLATES.items.find((entry) => entry.template.id === id);
  if (!item) throw new Error(`no template "${id}" in the fixture`);
  return item;
}
