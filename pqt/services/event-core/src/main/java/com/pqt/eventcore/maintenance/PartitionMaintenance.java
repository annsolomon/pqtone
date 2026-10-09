package com.pqt.eventcore.maintenance;

import com.pqt.eventcore.config.PqtProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Keeps daily event partitions ahead of time and applies the retention policy. */
@Component
public class PartitionMaintenance implements ApplicationRunner {
    private static final Logger log = LoggerFactory.getLogger(PartitionMaintenance.class);
    private final JdbcTemplate jdbc;
    private final PqtProperties props;

    public PartitionMaintenance(JdbcTemplate jdbc, PqtProperties props) {
        this.jdbc = jdbc;
        this.props = props;
    }

    @Override
    public void run(ApplicationArguments args) {
        ensure();
    }

    @Scheduled(cron = "0 7 * * * *", zone = "UTC")
    public void ensure() {
        Integer created = jdbc.queryForObject("SELECT pqt.ensure_event_partitions(7)", Integer.class);
        if (created != null && created > 0) log.info("created {} event partitions", created);
    }

    @Scheduled(cron = "0 20 3 * * *", zone = "UTC")
    public void retention() {
        Integer dropped = jdbc.queryForObject("SELECT pqt.drop_event_partitions_older_than(?)", Integer.class,
                props.retention().eventDays());
        log.info("retention: dropped {} event partitions older than {} days", dropped, props.retention().eventDays());
    }
}
