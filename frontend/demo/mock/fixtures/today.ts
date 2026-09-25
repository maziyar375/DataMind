/**
 * The day the demo's world ends — the one date everything else is relative to.
 *
 * The sales data runs up to this day, "the last 12 months" means the twelve
 * complete months before it, the sidebar's conversations were had in the days
 * before it, and the usage chart ends on it. It is fixed rather than "now"
 * because the story in the data is tied to the calendar: December is the
 * holiday peak and January the dip, and a date that slid forward with the
 * visitor's clock would move the numbers under the SQL that produced them.
 *
 * To move the demo forward, change this date **and** run
 * `demo/scripts/build-fixtures.sh`: the warehouse is regenerated for it and
 * every scripted statement is re-run against it, so the rows, the charts and
 * the sentences written about them move together. The build refuses to ship a
 * narrative whose claim no longer holds for the new data.
 */
export const DEMO_TODAY = '2026-09-25'
