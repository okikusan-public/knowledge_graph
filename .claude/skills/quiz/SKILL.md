---
name: quiz
description: Spaced repetition quiz using GraphRAG entities. Generates questions, grades answers, and tracks learning progress with optimal review intervals.
disable-model-invocation: false
argument-hint: [--topic "topic"] [--count 5] [--project project_name]
allowed-tools: Bash, Read
---

# Spaced Repetition Quiz

Generate and run quizzes from GraphRAG entities to strengthen long-term memory through retrieval practice, spaced repetition, and interleaving.

## Usage

```
/quiz
/quiz --topic "認知科学"
/quiz --count 3
/quiz --project project_a
```

## Workflow

### 1. Select Entities for Quiz

Run the quiz selection to get entities due for review:

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/quiz.py select -k ${COUNT:-5} ${TOPIC_FLAG} ${PROJECT_FLAG}
```

Where:
- `COUNT` defaults to 5, override with `--count N`
- `TOPIC_FLAG` is `--topic "topic"` if a topic argument was provided
- `PROJECT_FLAG` is `-p project_name` if a project was specified

Parse the arguments from the user's input:
- `--topic "X"` or bare text → use as `--topic`
- `--count N` or `-k N` → use as `-k`
- `--project X` or `-p X` → use as `-p`

This returns JSON with entities including: name, type, description, correct/incorrect counts, community, and related entities.

### 2. Generate Quiz Questions

For each selected entity, generate ONE question. Use these question types, varying across entities:

- **Definition**: "〜とは何ですか？" (test core understanding)
- **Relationship**: "〜と〜の関係は？" (use the entity's relations from the selection data)
- **Application**: "〜を実際にどう活用しますか？" (test practical understanding)
- **Comparison**: "〜と〜の違いは？" (use related entities from the same community)

**IMPORTANT**: Present questions ONE AT A TIME. Show the question, wait for the user's answer, then grade before showing the next question.

Show question like this:
```
**Q1/5** [エンティティ名 (タイプ)]
〜〜〜の質問〜〜〜
```

### 3. Grade Each Answer

After the user answers, grade on a 3-level scale using the entity's description and relationships as ground truth:

- **Correct** (score: 1.0): Core concept accurately captured
- **Partial** (score: 0.5): Some understanding but key elements missing
- **Incorrect** (score: 0.0): Fundamental misunderstanding or no answer

Provide brief feedback: what was right, what was missing, and the key point from the graph.

### 4. Record Each Result

After grading, record the result immediately:

```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/quiz.py record ${PROJECT_FLAG} --json '{"entity_name": "ENTITY_NAME", "is_correct": true/false, "question": "THE_QUESTION", "user_answer": "USER_ANSWER", "score": 0.0-1.0, "feedback": "FEEDBACK"}'
```

- `is_correct`: true if score >= 0.5, false otherwise
- Escape JSON strings properly (especially quotes in Japanese text)

### 5. Show Summary

After all questions, show a summary:

```
## クイズ結果

| # | エンティティ | 結果 | 次回復習 |
|---|---|---|---|
| 1 | Entity A | ⭕ 正解 | 4日後 |
| 2 | Entity B | ❌ 不正解 | 1日後 |
| 3 | Entity C | △ 部分正解 | 2日後 |

正答率: 2/3 (67%)
```

## How Spaced Repetition Works

- Correct answers → review interval doubles (1→2→4→8→16 days, max 90 days)
- Incorrect answers → interval resets to 1 day
- Entities with more incorrect answers get prioritized
- Never-quizzed entities get medium priority
- Different entity types are mixed (interleaving) to strengthen pattern recognition

## Stats

To check quiz statistics, run:
```bash
python ${CLAUDE_SKILL_DIR}/../../../scripts/quiz.py stats ${PROJECT_FLAG}
```
