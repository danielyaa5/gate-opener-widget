import CoreLocation

/// Retire location monitoring installed by the previous app build.
enum GateLocationCleanup {
    static func disableLegacyMonitoring() {
        let manager = CLLocationManager()
        for region in manager.monitoredRegions where region.identifier == "gate-home-region" {
            manager.stopMonitoring(for: region)
        }
        manager.stopUpdatingLocation()
        manager.stopMonitoringSignificantLocationChanges()
        _ = GateSettings.saveSharedData(Data([1]), account: "phoneAwayPausedV1")
        _ = GateSettings.saveSharedData(Data(), account: "phoneAwayConfigurationV1")
    }
}
