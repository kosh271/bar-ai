function widget:GetInfo()
    return {
        name = "Studio Match Statistics",
        desc = "Writes team totals to the headless match log",
        layer = -100000,
        enabled = true,
    }
end

local fields = {
    "damageDealt", "damageReceived", "metalProduced", "energyProduced",
    "metalUsed", "energyUsed", "metalReceived", "energyReceived",
    "unitsProduced", "unitsKilled", "unitsDied",
}

local function report()
    if Spring.GetGameFrame() <= 0 then
        return
    end
    local gaia = Spring.GetGaiaTeamID()
    for _, team in ipairs(Spring.GetTeamList()) do
        if team ~= gaia then
            local count = Spring.GetTeamStatsHistory(team)
            local samples = count and Spring.GetTeamStatsHistory(team, count, count)
            local sample = samples and samples[1]
            if sample then
                local totals = {}
                for _, field in ipairs(fields) do
                    if type(sample[field]) == "number" then
                        totals[#totals + 1] = string.format("%s=%.6f", field, sample[field])
                    end
                end
                Spring.Echo(string.format("Team %d stats: %s", team, table.concat(totals, ", ")))
            end
        end
    end
end

function widget:GameFrame(frame)
    if frame > 1 and frame % 900 == 1 then
        report()
    end
end

function widget:GameOver()
    report()
end

function widget:Shutdown()
    report()
end
