use serde::Deserialize;


#[derive(Deserialize)]
#[serde(untagged)]
pub enum AnyEvent {
    Known(Event),
    Unknown(serde_json::Value),
}

#[derive(Deserialize)]
pub struct Envelope {
    pub seq: u64,
    #[serde(default)]
    pub replay: bool,
    pub agent_id: Option<String>,
    #[serde(flatten)]
    pub event: AnyEvent,
}

#[derive(Deserialize)]
#[serde(tag = "type", content = "data")]
pub enum Event {
    #[serde(rename = "block.delta")]
    BlockDelta { text: String },
    #[serde(rename = "agent.upsert")]
    AgentUpsert { name: String, role: String, tools: Vec<Tool> },
    #[serde(rename = "agent.status")]
    AgentStatus { status: String },
    #[serde(rename = "agent.link")]
    AgentLink { kind: String, source_agent: String, target_agent: String },
    #[serde(rename = "block.start")]
    BlockStart { kind: String, tool_call: Option<ToolCall> },
    #[serde(rename = "turn.end")]
    TurnEnd { finish_reason: String, usage: serde_json::Value },
    #[serde(rename = "error")]
    Error { code: String, message: String },
    // nothing after Error: no #[serde(other)], no fallback variant
}


#[derive(Deserialize)]
struct Tool {
    name: String,
}

#[derive(Deserialize)]
struct ToolCall {
    name: String,
}



impl Event {
    /// Wire name of the event type, for the summary line.
    pub(crate) fn type_name(&self) -> &'static str {
        match self {
            Event::BlockDelta { .. } => "block.delta",
            Event::AgentUpsert { .. } => "agent.upsert",
            Event::AgentStatus { .. } => "agent.status",
            Event::AgentLink { .. } => "agent.link",
            Event::BlockStart { .. } => "block.start",
            Event::TurnEnd { .. } => "turn.end",
            Event::Error { .. } => "error",
        }
    }

    pub(crate) fn detail(&self) -> String {
        match self {
            Event::AgentUpsert { name, role, tools } => {
                let names: Vec<&str> = tools.iter().map(|t| t.name.as_str()).collect();
                format!("{name} role={role} tools={names:?}")
            }
            Event::AgentStatus { status } => status.clone(),
            Event::AgentLink { kind, source_agent, target_agent } => {
                format!("{kind} {source_agent} -> {target_agent}")
            }
            Event::BlockStart { kind, tool_call } => {
                let tool = tool_call.as_ref().map_or("", |t| t.name.as_str());
                format!("{kind} {tool}")
            }
            Event::TurnEnd { finish_reason, usage } => format!("{finish_reason} {usage}"),
            Event::Error { code, message } => format!("{code}: {message}"),
            Event::BlockDelta { .. } => { todo!() }
        }
    }
}

impl AnyEvent {
    pub fn type_name(&self) -> String {
        match self {
            AnyEvent::Known(e) => e.type_name().to_string(),
            AnyEvent::Unknown(v) => v["type"].as_str().unwrap_or("unknown").to_string(),
        }
    }

    pub fn detail(&self) -> String {
        match self {
            AnyEvent::Known(e) => e.detail(),
            AnyEvent::Unknown(v) => v.get("data").map(|d| d.to_string()).unwrap_or_default(),
        }
    }
}