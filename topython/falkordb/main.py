import asyncio
from falkordb.asyncio import FalkorDB
from redis.asyncio import BlockingConnectionPool


async def main():
    # Connect to FalkorDB
    pool = BlockingConnectionPool(max_connections=16, timeout=None, decode_responses=True)
    db = FalkorDB(connection_pool=pool)

    # Select the social graph
    g = db.select_graph('social')

    # Execute query asynchronously
    result = await g.query('UNWIND range(0, 100) AS i CREATE (n {v:1}) RETURN n LIMIT 10')

    # Process results
    for n in result.result_set:
        print(n)

    # Run multiple queries concurrently
    tasks = [
        g.query('MATCH (n) WHERE n.v = 1 RETURN count(n) AS count'),
        g.query('CREATE (p:Person {name: "Alice"}) RETURN p'),
        g.query('CREATE (p:Person {name: "Bob"}) RETURN p')
    ]

    results = await asyncio.gather(*tasks)

    # Process concurrent results
    print(f"Node count: {results[0].result_set[0][0]}")
    print(f"Created Alice: {results[1].result_set[0][0]}")
    print(f"Created Bob: {results[2].result_set[0][0]}")

    # Close the connection when done
    await pool.aclose()


# Run the async example
if __name__ == "__main__":
    asyncio.run(main())