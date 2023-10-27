# gNMI proto files

### The ```gnmi.proto``` inside this folder is the merge of these two files:  
- https://raw.githubusercontent.com/openconfig/gnmi/master/proto/gnmi/gnmi.proto
- https://raw.githubusercontent.com/openconfig/gnmi/master/proto/gnmi_ext/gnmi_ext.proto

### The script ```proto_compile.sh``` requires:
- The Python package ```grpcio-tools``` must be installed.
- Must be run from this folder.
- The project package ```/src/gnmi_pb2``` must exist.
