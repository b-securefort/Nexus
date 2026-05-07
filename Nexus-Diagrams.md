# Nexus — How It Works

Visual overview of the three core systems that power Nexus, and how they connect.

---

## 1. Tools Engine & Orchestrator

When an engineer asks a question, the Orchestrator figures out *what to do* and the Tools Engine *does it* — looping up to 15 times until the answer is complete.

```mermaid
flowchart LR
    User([👤 Engineer]):::user -->|asks a question| Orch[🧠 Orchestrator]:::core

    Orch -->|"what do I need?"| Plan[Plan Steps]:::muted
    Plan --> Pick[Pick Tools]:::muted

    Pick --> TE[⚙️ Tools Engine]:::core

    TE --> T1[🔍 Search KB]:::tool
    TE --> T2[📊 Query Azure]:::tool
    TE --> T3[🗺️ Generate Diagram]:::tool
    TE --> T4[📝 Create File]:::tool
    TE --> T5[🛡️ Check Compliance]:::tool

    T1 & T2 & T3 & T4 & T5 -->|results| Orch

    Orch -->|"need more info?"| Pick
    Orch -->|"done — here's the answer"| User

    classDef user fill:#86BC25,stroke:#046A38,color:#fff,stroke-width:2px
    classDef core fill:#002776,stroke:#002776,color:#fff,stroke-width:2px
    classDef muted fill:#f5f5f5,stroke:#A7A8AA,color:#4B4B4D
    classDef tool fill:#e8f5e9,stroke:#86BC25,color:#000,stroke-width:1.5px
```

> **Key idea**: The Orchestrator doesn't just call one tool — it *reasons* about which tools to chain together, reviews intermediate results, and keeps going until the question is fully answered.

---

## 2. How Learning Works

Nexus gets smarter every time it's used. Knowledge flows in from team contributions *and* from conversations — building a living memory that the whole team benefits from.

```mermaid
flowchart TB
    subgraph Input["📥 Knowledge Sources"]
        direction LR
        ADR[ADRs & Decisions]:::source
        RB[Runbooks]:::source
        PAT[Patterns & Standards]:::source
        CONV[Past Conversations]:::source
    end

    Input --> KB[(🧠 Team Knowledge Base)]:::core

    subgraph Loop["🔄 Conversation Learning"]
        direction LR
        Ask([Engineer asks]):::user --> Nexus[Nexus responds]:::core
        Nexus --> Outcome{Useful?}:::decision
        Outcome -->|yes| Capture[Capture insight]:::muted
        Outcome -->|no| Refine[Refine approach]:::muted
    end

    Capture --> KB
    Refine --> KB
    KB --> Nexus

    subgraph Benefit["✨ Team Impact"]
        direction LR
        B1[New joiners learn faster]:::benefit
        B2[Answers improve over time]:::benefit
        B3[Knowledge never leaves]:::benefit
    end

    KB --> Benefit

    classDef source fill:#e8f5e9,stroke:#86BC25,color:#000,stroke-width:1.5px
    classDef core fill:#002776,stroke:#002776,color:#fff,stroke-width:2px
    classDef user fill:#86BC25,stroke:#046A38,color:#fff,stroke-width:2px
    classDef decision fill:#fff,stroke:#0097A9,color:#0097A9,stroke-width:2px
    classDef muted fill:#f5f5f5,stroke:#A7A8AA,color:#4B4B4D
    classDef benefit fill:#86BC25,stroke:#046A38,color:#fff,stroke-width:1.5px

    style Input fill:#f0f0f0,stroke:#A7A8AA,color:#000
    style Loop fill:#f0f0f0,stroke:#A7A8AA,color:#000
    style Benefit fill:#f0f0f0,stroke:#A7A8AA,color:#000
```

> **Key idea**: Nexus doesn't just answer from static docs — it learns from every interaction. The more your team uses it, the smarter it gets.

---

## 3. How Skills Work

A Skill is a markdown file that tells Nexus *who to be*, *how to think*, and *what tools to use*. Switching skills completely changes Nexus's behaviour — like giving it a new role.

```mermaid
flowchart TB
    Eng([👤 Engineer]):::user -->|selects a skill| Switch[🔀 Skill Selector]:::core

    Switch --> S1[💬 Chat with KB]:::skill
    Switch --> S2[🏗️ Architect Mode]:::teal
    Switch --> S3[🛡️ Security Reviewer]:::darkgreen
    Switch --> S4[✏️ Your Custom Skill]:::custom

    subgraph Inside["What a Skill Defines"]
        direction TB
        Persona[🎭 Persona & Tone]:::muted
        Rules[📏 Rules & Constraints]:::muted
        Tools[🔧 Allowed Tools]:::muted
        Output[📄 Output Format]:::muted
    end

    S1 & S2 & S3 & S4 --> Inside
    Inside --> Nexus[🧠 Nexus Responds]:::core
    Nexus --> Result([Tailored Answer]):::user

    subgraph Create["🚀 Creating a Skill"]
        direction LR
        Write[Write a .md file]:::benefit --> Push[Push to Git]:::benefit --> Live[Nexus picks it up]:::benefit
    end

    classDef user fill:#86BC25,stroke:#046A38,color:#fff,stroke-width:2px
    classDef core fill:#002776,stroke:#002776,color:#fff,stroke-width:2px
    classDef skill fill:#e8f5e9,stroke:#86BC25,color:#000,stroke-width:1.5px
    classDef teal fill:#e0f7fa,stroke:#0097A9,color:#000,stroke-width:1.5px
    classDef darkgreen fill:#e8f5e9,stroke:#046A38,color:#000,stroke-width:1.5px
    classDef custom fill:#fff,stroke:#A7A8AA,color:#4B4B4D,stroke-width:1.5px,stroke-dasharray:5 5
    classDef muted fill:#f5f5f5,stroke:#A7A8AA,color:#4B4B4D
    classDef benefit fill:#86BC25,stroke:#046A38,color:#fff,stroke-width:1.5px

    style Inside fill:#f0f0f0,stroke:#A7A8AA,color:#000
    style Create fill:#f0f0f0,stroke:#A7A8AA,color:#000
```

> **Key idea**: Skills are just markdown. Any engineer can create one — no code, no deploy. Your team's best practices become living, enforceable AI behaviour.

---

## 4. Combined — The Full Picture

How all three systems connect: Skills shape *who* Nexus is, the Orchestrator & Tools Engine handle *what* it does, and Learning makes it *smarter over time*.

```mermaid
flowchart TB
    Eng([👤 Engineer]):::user

    %% Skills layer
    Eng -->|"picks a role"| Skills

    subgraph Skills["🎭 SKILLS"]
        direction LR
        S1[Chat with KB]:::skill
        S2[Architect]:::teal
        S3[Security]:::darkgreen
        S4[Custom]:::custom
    end

    Skills -->|"persona + rules + tools"| Orch

    %% Orchestrator layer
    subgraph Engine["⚙️ ORCHESTRATOR + TOOLS ENGINE"]
        direction TB
        Orch[🧠 Orchestrator]:::core
        Orch -->|plan & pick| Tools

        subgraph Tools["Available Tools"]
            direction LR
            T1[🔍 Search KB]:::tool
            T2[📊 Query Azure]:::tool
            T3[🗺️ Diagrams]:::tool
            T4[📝 Files]:::tool
            T5[🛡️ Compliance]:::tool
        end

        Tools -->|results| Orch
        Orch -->|"need approval?"| Gate{🔒 Gate}:::decision
        Gate -->|approved| Execute[Execute]:::muted
        Execute -->|output| Orch
    end

    %% Knowledge layer
    subgraph Knowledge["🧠 KNOWLEDGE + LEARNING"]
        direction LR
        KB[(Team KB)]:::core
        Learn[📚 Learning Loop]:::benefit
        KB <-->|"read & write"| Learn
    end

    Orch <-->|"search docs\nrecord insights"| Knowledge

    %% Output
    Orch -->|"complete answer"| Response([📋 Response to Engineer]):::user

    %% Styling
    classDef user fill:#86BC25,stroke:#046A38,color:#fff,stroke-width:2px
    classDef core fill:#002776,stroke:#002776,color:#fff,stroke-width:2px
    classDef skill fill:#e8f5e9,stroke:#86BC25,color:#000,stroke-width:1.5px
    classDef teal fill:#e0f7fa,stroke:#0097A9,color:#000,stroke-width:1.5px
    classDef darkgreen fill:#e8f5e9,stroke:#046A38,color:#000,stroke-width:1.5px
    classDef custom fill:#fff,stroke:#A7A8AA,color:#4B4B4D,stroke-width:1.5px,stroke-dasharray:5 5
    classDef tool fill:#e8f5e9,stroke:#86BC25,color:#000,stroke-width:1.5px
    classDef decision fill:#fff,stroke:#0097A9,color:#0097A9,stroke-width:2px
    classDef muted fill:#f5f5f5,stroke:#A7A8AA,color:#4B4B4D
    classDef benefit fill:#86BC25,stroke:#046A38,color:#fff,stroke-width:1.5px

    style Skills fill:#f0f0f0,stroke:#A7A8AA,color:#000
    style Engine fill:#e8e8e8,stroke:#A7A8AA,color:#000
    style Tools fill:#f0f0f0,stroke:#A7A8AA,color:#000
    style Knowledge fill:#f0f0f0,stroke:#A7A8AA,color:#000
```

> **The full loop**: An engineer picks a Skill (shaping Nexus's persona), asks a question, the Orchestrator plans and calls tools, checks the Knowledge Base, asks for approval when needed, delivers the answer — and records what it learned for next time. Every cycle makes Nexus smarter.

---

*Nexus Concept Diagrams — May 2026*
