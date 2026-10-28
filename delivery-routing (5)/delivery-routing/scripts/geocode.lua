-- osm2pgsql flex style: extracts what address search needs from the .osm.pbf.
-- Works with osm2pgsql 1.8+ and 2.x. Tables go to the schema in DR_GEO_SCHEMA
-- (a staging schema; geocode-build.sql turns them into the search index).

local schema = os.getenv("DR_GEO_SCHEMA") or "dr_geo_import"

local function ids() return { type = "any", type_column = "osm_type", id_column = "osm_id" } end

local places = osm2pgsql.define_table({
    name = "places", schema = schema, ids = ids(),
    columns = {
        { column = "names",      type = "text", not_null = true },
        { column = "name",       type = "text", not_null = true },
        { column = "place",      type = "text" },
        { column = "population", type = "text" },
        { column = "postcode",   type = "text" },
        { column = "geom",       type = "point", projection = 4326, not_null = true },
    } })

local pois = osm2pgsql.define_table({
    name = "pois", schema = schema, ids = ids(),
    columns = {
        { column = "names",       type = "text", not_null = true },
        { column = "name",        type = "text", not_null = true },
        { column = "category",    type = "text" },
        { column = "housenumber", type = "text" },
        { column = "street",      type = "text" },
        { column = "postcode",    type = "text" },
        { column = "city",        type = "text" },
        { column = "geom",        type = "point", projection = 4326, not_null = true },
    } })

local streets = osm2pgsql.define_table({
    name = "streets", schema = schema, ids = ids(),
    columns = {
        { column = "names",   type = "text", not_null = true },
        { column = "name",    type = "text", not_null = true },
        { column = "highway", type = "text" },
        { column = "geom",    type = "linestring", projection = 4326, not_null = true },
    } })

local addresses = osm2pgsql.define_table({
    name = "addresses", schema = schema, ids = ids(),
    columns = {
        { column = "housenumber", type = "text" },
        { column = "housename",   type = "text" },     -- common in Kerala: "Rose Villa"
        { column = "addr_full",   type = "text" },     -- addr:full free-text address
        { column = "street",      type = "text" },
        { column = "place",       type = "text" },
        { column = "postcode",    type = "text" },
        { column = "city",        type = "text" },
        { column = "geom",        type = "point", projection = 4326, not_null = true },
    } })

local admin = osm2pgsql.define_table({
    name = "admin", schema = schema, ids = ids(),
    columns = {
        { column = "name",        type = "text", not_null = true },
        { column = "admin_level", type = "int" },
        { column = "postcode",    type = "text" },
        { column = "geom",        type = "multipolygon", projection = 4326, not_null = true },
    } })

local PLACE_TYPES = {
    city = true, town = true, village = true, hamlet = true, suburb = true,
    neighbourhood = true, quarter = true, locality = true, borough = true, isolated_dwelling = true,
}
local STREET_TYPES = {
    motorway = true, trunk = true, primary = true, secondary = true, tertiary = true,
    unclassified = true, residential = true, living_street = true, service = true, pedestrian = true,
    motorway_link = true, trunk_link = true, primary_link = true, secondary_link = true, tertiary_link = true,
    road = true, track = false, footway = false, path = false,
}
-- keys that make a named object a point of interest (landmark, shop, building ...)
local POI_KEYS = { "amenity", "shop", "tourism", "office", "leisure", "healthcare", "craft",
                   "historic", "railway", "public_transport", "aeroway", "man_made", "building", "landuse" }
local SKIP_POI = { ["railway=rail"] = true, ["railway=abandoned"] = true, ["landuse=farmland"] = true,
                   ["landuse=meadow"] = true, ["building=yes"] = false, ["man_made=pipeline"] = true }

local function all_names(tags)
    local out, seen = {}, {}
    for _, k in ipairs({ "name", "name:en", "alt_name", "old_name", "official_name", "short_name", "loc_name",
                         "name:ml", "name:ta", "name:kn", "name:te", "name:hi" }) do
        local v = tags[k]
        if v and v ~= "" and not seen[v] then seen[v] = true; out[#out + 1] = v end
    end
    return table.concat(out, " ; ")
end

local function display_name(tags) return tags["name:en"] or tags.name end

local function poi_category(tags)
    for _, k in ipairs(POI_KEYS) do
        local v = tags[k]
        if v and v ~= "no" then
            local c = k .. "=" .. v
            if SKIP_POI[c] then return nil end
            return c
        end
    end
    return nil
end

local function point_of(object, kind)
    if kind == "node" then return object:as_point() end
    if kind == "way" then
        if object.is_closed then return object:as_polygon():centroid() end
        return object:as_linestring():centroid()
    end
    return object:as_multipolygon():centroid()
end

local function handle(object, kind)
    local t = object.tags
    local name = display_name(t)

    -- places (cities, towns, suburbs, villages ...)
    if name and t.place and PLACE_TYPES[t.place] then
        local g = point_of(object, kind)
        if g then
            places:insert({ names = all_names(t), name = name, place = t.place, population = t.population,
                            postcode = t["addr:postcode"] or t.postal_code, geom = g })
        end
    end

    -- administrative areas (state = 4, district = 5, sub-district = 6 ...)
    if kind == "relation" and t.boundary == "administrative" and t.admin_level and name then
        local lvl = tonumber(t.admin_level)
        if lvl and lvl >= 4 and lvl <= 10 then
            admin:insert({ name = name, admin_level = lvl, postcode = t.postal_code, geom = object:as_multipolygon() })
        end
        return
    end

    -- named streets
    if kind == "way" and name and t.highway and STREET_TYPES[t.highway] and t.area ~= "yes" then
        streets:insert({ names = all_names(t), name = name, highway = t.highway, geom = object:as_linestring() })
        return
    end

    -- points of interest
    if name and not t.place and not t.boundary then
        local cat = poi_category(t)
        if cat then
            local g = point_of(object, kind)
            if g then
                pois:insert({ names = all_names(t), name = name, category = cat,
                              housenumber = t["addr:housenumber"], street = t["addr:street"],
                              postcode = t["addr:postcode"], city = t["addr:city"], geom = g })
            end
            return
        end
    end

    -- house addresses: a number, a house name, or a free-text address
    local hn, hname, full = t["addr:housenumber"], t["addr:housename"], t["addr:full"]
    if hn or hname or full then
        local g = point_of(object, kind)
        if g then
            addresses:insert({ housenumber = hn, housename = hname, addr_full = full,
                               street = t["addr:street"], place = t["addr:place"],
                               postcode = t["addr:postcode"], city = t["addr:city"], geom = g })
        end
    end
end

function osm2pgsql.process_node(object) handle(object, "node") end
function osm2pgsql.process_way(object) handle(object, "way") end
function osm2pgsql.process_relation(object)
    local t = object.tags
    if t.type == "multipolygon" or t.type == "boundary" then handle(object, "relation") end
end
