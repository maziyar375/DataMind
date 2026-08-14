-- The read-only role the connector is meant to be pointed at, and the reason
-- the Oracle target is worth having in Compose at all.
--
-- `analytics_ro` is deliberately as poor as a role can be and still work:
-- `CREATE SESSION` plus one `GRANT SELECT` per table, and **no roles at all** —
-- not `CONNECT`, not `RESOURCE`, and specifically not `SELECT_CATALOG_ROLE`.
-- That last one is the whole question the catalog-comment feature had to answer
-- on Oracle: `ALL_TAB_COMMENTS` and `ALL_COL_COMMENTS` show exactly what the
-- connecting role was granted, so a role with nothing still reads the comments
-- on the tables it can see, and nothing else (docs/catalog-metadata-plan.md §9).
--
-- If you widen this user, that property stops being tested. Point a *new* user
-- at the database instead.
ALTER SESSION SET CONTAINER = XEPDB1;

CREATE USER analytics_ro IDENTIFIED BY analytics_ro;
GRANT CREATE SESSION TO analytics_ro;

GRANT SELECT ON sales.customers   TO analytics_ro;
GRANT SELECT ON sales.products    TO analytics_ro;
GRANT SELECT ON sales.orders      TO analytics_ro;
GRANT SELECT ON sales.order_items TO analytics_ro;

-- Not granted on purpose: sales."Orders" (the quoted twin) and the
-- sales.recent_orders view. The first keeps the identifier-case hazard visible
-- to an owner connection without putting it in the demo's snapshot; the second
-- would be filtered by TABLE_TYPE anyway, and leaving it ungranted means the
-- connector is doing the filtering rather than the grant.
EXIT;
