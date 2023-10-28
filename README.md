# gnmi-exporter

This application allows you to export gNMI streaming telemetries from network devices to Prometheus, 
in a data-model-aware way.  
The application is designed around the "DataModel-conscious Plugin module" concept:  
- A plugin takes care of processing data from a given set of Paths in the OpenConfig Schema, shaping the 
resulting telemetry stream in the most convenient way for the user.
- The gNMI client module can load multiple plugin instances, one for each set of Paths to be subscribed.
- The application core can run multiple gNMI client instances, one for each configured target device.
- When Prometheus server scrapes the application, the exporter collects the processed data from plugins and exposes
it via the http server.

## Project Goals
**Coming soon!**  
This project is currently under development. 
Features and database schema can change suddenly without notice.  
**PLEASE DO NOT USE IN PRODUCTION ENVIRONMENTS**

