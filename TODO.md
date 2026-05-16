## TODO

[x] rename the graph / graph-out folder references and variables

[] search implementation (u have plans)
[] e2e test of search ( semantic + graph)

[] (this is in search microservice) memory eviction if graph is not accessed for 10min
can be implemented with decorators, this is gonna go to microservice package
'''python
@cached(cache=TTLCache(maxsize=3, ttl=20))
def fetch_data(id):
....
'''

[] api and cli usage of the lib in separate package