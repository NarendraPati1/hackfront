# Neo4j Graph Schema — for Cypher generation

Use ONLY the labels, relationship types, and directions below. Never invent a
label or relationship that isn't listed here.

## Node labels and properties

- `Company {name, industry}`
- `Product {name, type, status, description}`
- `Team {name, department}`
- `Employee {name, role, experience}`
- `Feature {name, description}`
- `Ticket {key, title, status, priority, description}`
- `Issue {name, severity, description}`
- `Message {id, channel, content}`
- `Decision {description, created_at, reason}`

## Relationships (direction matters — copy exactly)

- `(:Company)-[:OWNS]->(:Product)`
- `(:Team)-[:WORKS_ON]->(:Product)`
- `(:Employee)-[:MEMBER_OF]->(:Team)`
- `(:Employee)-[:MANAGES]->(:Product)`
- `(:Product)-[:HAS_FEATURE]->(:Feature)`
- `(:Feature)-[:HAS_TICKET]->(:Ticket)`
- `(:Employee)-[:ASSIGNED_TO]->(:Ticket)`
- `(:Ticket)-[:BLOCKS]->(:Ticket)`  — the SOURCE ticket blocks the TARGET ticket
  (source is the upstream root cause, target is the one waiting on it)
- `(:Ticket)-[:CAUSED_BY]->(:Issue)`
- `(:Employee)-[:POSTED]->(:Message)`
- `(:Ticket)-[:DISCUSSED_IN]->(:Message)`
- `(:Message)-[:LED_TO]->(:Decision)`
- `(:Decision)-[:AFFECTS]->(:Ticket)`
- `(:Decision)-[:MADE_BY]->(:Employee)`

## Rules for generated Cypher

1. READ-ONLY. Never use `CREATE`, `MERGE`, `DELETE`, `DETACH`, `SET`, `REMOVE`,
   or `DROP`. If the question requires writing data, say you cannot do that
   with this tool instead of generating a query.
2. Use only `MATCH`, `OPTIONAL MATCH`, `WHERE`, `WITH`, `RETURN`, `ORDER BY`,
   `LIMIT`, and aggregation functions (`count`, `collect`, etc).
3. Always alias returned fields with `AS` so results are readable
   (e.g. `RETURN t.key AS ticket_key`, not bare `t`).
4. Add `LIMIT 25` unless the question clearly wants a single aggregate value.
5. Match property values by exact string as given in the question — do not
   guess at IDs or keys that weren't mentioned.
6. If unsure whether a label/relationship exists for what's being asked,
   write a query that returns nothing meaningful rather than guessing wrong
   labels — an empty result is better than a wrong one.

## Example questions and correct Cypher

Q: "Which employees work on PhonePe?"
```
MATCH (e:Employee)-[:MEMBER_OF]->(:Team)-[:WORKS_ON]->(p:Product {name: "PhonePe"})
RETURN DISTINCT e.name AS employee
```

Q: "List all high priority tickets that aren't done."
```
MATCH (t:Ticket)
WHERE t.priority = "High" AND t.status <> "Done"
RETURN t.key AS ticket_key, t.title AS title, t.status AS status
LIMIT 25
```

Q: "Who made the decision to increase payment gateway capacity?"
```
MATCH (d:Decision {description: "Increase payment gateway capacity during peak traffic."})-[:MADE_BY]->(e:Employee)
RETURN e.name AS decided_by
```

Q: "How many tickets does each team have open?"
```
MATCH (e:Employee)-[:MEMBER_OF]->(team:Team)
MATCH (e)-[:ASSIGNED_TO]->(t:Ticket)
WHERE t.status <> "Done"
RETURN team.name AS team, count(DISTINCT t) AS open_tickets
ORDER BY open_tickets DESC
```