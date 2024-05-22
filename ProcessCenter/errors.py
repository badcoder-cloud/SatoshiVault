import faust 

# log and proceed
faust_proceed_errors = (
    faust.exceptions.Skip,
    faust.exceptions.FaustWarning,
    faust.exceptions.AlreadyConfiguredWarning,
    faust.exceptions.FaustPredicate,
    faust.exceptions.SameNode,
    faust.exceptions.PartitionsMismatch
)

# retry then log
faust_backup_errors = (
    faust.exceptions.NotReady, 
    faust.exceptions.ConsumerNotStarted, 
)

# write down messages and proceed
faust_message_errors = (
    faust.exceptions.FaustPredicate,
    faust.exceptions.ValidationError,   
    faust.exceptions.DecodeError,
    faust.exceptions.KeyDecodeError,
    faust.exceptions.ValueDecodeError,
    faust.exceptions.ProducerSendError,
)

# log, shutdown faust
faust_shutdown_errors = (
    faust.exceptions.SecurityError,
    faust.exceptions.ImproperlyConfigured,
    faust.exceptions.ConsistencyError,
)


# insert into database errors wrapper