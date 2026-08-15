# VERIS DESIGN PRINCIPLES

## 1. Simple on the Surface

The underlying intelligence may be extremely sophisticated.

The user experience should not be.

---

## 2. Connections, Not Documents

Do not make the user hunt through documents to understand relationships.

Show relationships directly.

---

## 3. Evidence Is Always Nearby

When Veris says:

> "These are connected."

the user should be able to quickly understand:

> "Why?"

Evidence should never feel hidden.

---

## 4. Explain the Relationship

Whenever practical, describe the type of connection.

Examples:

* Implements
* Supports
* Requires
* Teaches
* Validates
* Depends on
* Conflicts with
* Supersedes
* Related to
* Potentially affected by

Do not force these relationship types into the architecture prematurely. Discover the appropriate semantic model.

---

## 5. Surface What Humans Miss

The product should prioritize insights that are difficult to see manually.

Examples:

### Potential Gap

Something expected appears to be missing.

### Potential Conflict

Two sources appear inconsistent.

### Dependency

Changing one item may affect another.

### Drift

Two connected artifacts may no longer express the same concept.

### Change Impact

A new external requirement may affect multiple organizational artifacts.

---

## 6. Don't Overstate

Use language proportional to evidence.

Prefer:

> Potential gap identified.

over:

> Your organization is noncompliant.

Prefer:

> These sources appear inconsistent.

over:

> These policies conflict.

Unless the evidence truly supports the stronger statement.

---

## 7. Progressive Disclosure

Start simple.

Allow users to go deeper when they want:

**Insight**

↓

**Relationship**

↓

**Evidence**

↓

**Source**

The default experience should not overwhelm the user.

---

## 8. Search Is Not the Destination

Search may be important.

But the product should not stop at retrieval.

The value begins when Veris explains relationships between retrieved knowledge.

---

## 9. Every Insight Should Lead Somewhere

A finding should allow the user to:

* Investigate
* View evidence
* Identify affected knowledge
* Assign review
* Record a decision
* Continue exploring

Avoid dead-end AI responses.

---

## 10. The Interface Should Feel Like Exploration

Veris should encourage questions such as:

> What connects to this?

> What depends on this?

> What changed?

> What's missing?

> What's inconsistent?

> Why are these related?

> What else should I look at?

This is more important than a particular UI pattern.
