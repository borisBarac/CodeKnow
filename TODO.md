## TODO

- search implementation (u have plans)
- full e2e test

- api and cli usage of the lib in separate package

- memory eviction if graph is not accessed for 10min
can be implemented with decorators, this is gonna go to microservice package
'''python
@cached(cache=TTLCache(maxsize=3, ttl=20))
def fetch_data(id):
....
'''
