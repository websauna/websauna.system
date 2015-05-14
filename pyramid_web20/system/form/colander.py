import inspect
import logging
import itertools
import colander
from colanderalchemy.schema import SQLAlchemySchemaNode
from pyramid_web20.utils.jsonb import JSONBProperty

import colander
from colander import (Mapping,
                      drop,
                      required,
                      SchemaNode,
                      Sequence)
from sqlalchemy import (Boolean,
                        Date,
                        DateTime,
                        Enum,
                        Float,
                        inspect,
                        Integer,
                        String,
                        Numeric,
                        Time)
import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import (FetchedValue, ColumnDefault, Column)
from sqlalchemy.orm import (ColumnProperty, RelationshipProperty)


log = logger = logging.getLogger(__name__)


class PropertyAwareSQLAlchemySchemaNode(SQLAlchemySchemaNode):
    """Allow forming of JSON'ed properties besides SQL Alchemy fields."""

    def dictify(self, obj):
        """Extended to handle JSON properties."""

        dict_ = {}
        for node in self:

            name = node.name
            try:
                if JSONBProperty.is_json_property(obj, name):
                    value = getattr(obj, name)
                else:
                    getattr(self.inspector.column_attrs, name)
                    value = getattr(obj, name)

            except AttributeError:
                try:
                    prop = getattr(self.inspector.relationships, name)
                    if prop.uselist:
                        value = [self[name].children[0].dictify(o)
                                 for o in getattr(obj, name)]
                    else:
                        o = getattr(obj, name)
                        value = None if o is None else self[name].dictify(o)
                except AttributeError:
                    # The given node isn't part of the SQLAlchemy model
                    msg = 'SQLAlchemySchemaNode.dictify: %s not found on %s'
                    logger.debug(msg, name, self)
                    continue

            # SQLAlchemy mostly converts values into Python types
            #  appropriate for appstructs, but not always.  The biggest
            #  problems are around `None` values so we're dealing with
            #  those here.  All types should accept `colander.null` so
            #  we mostly change `None` into that.

            if value is None:
                if isinstance(node.typ, colander.String):
                    # colander has an issue with `None` on a String type
                    #  where it translates it into "None".  Let's check
                    #  for that specific case and turn it into a
                    #  `colander.null`.
                    dict_[name] = colander.null
                else:
                    # A specific case this helps is with Integer where
                    #  `None` is an invalid value.  We call serialize()
                    #  to test if we have a value that will work later
                    #  for serialization and then allow it if it doesn't
                    #  raise an exception.  Hopefully this also catches
                    #  issues with user defined types and future issues.
                    try:
                        node.serialize(value)
                    except:
                        dict_[name] = colander.null
                    else:
                        dict_[name] = value
            else:
                dict_[name] = value

        return dict_

    def objectify(self, dict_, context=None):
        """Extended to handle JSON properties."""

        mapper = self.inspector
        context = mapper.class_() if context is None else context

        print("Objectifying ", dict_)

        # If our schema and widgets wants pass us back full objects instead of theri dictified versions, let them pass through
        if sqlalchemy.inspect(dict_, raiseerr=False) is not None:
            return dict_

        marker = object()
        orig = dict_.get("_orig", marker)
        if orig is not marker:
            return orig

        for attr in dict_:
            if JSONBProperty.is_json_property(context, attr):
                # TODO: Nested or sequences not supported
                value = dict_[attr]
                setattr(context, attr, value)
            elif mapper.has_property(attr):
                prop = mapper.get_property(attr)
                if hasattr(prop, 'mapper'):
                    cls = prop.mapper.class_

                    print("Mapper Objectifying ", prop, attr, context)

                    if prop.uselist:
                        # Sequence of objects
                        value = [self[attr].children[0].objectify(obj)
                                 for obj in dict_[attr]]
                    else:
                        # Single object
                        value = self[attr].objectify(dict_[attr])
                else:
                     value = dict_[attr]
                     if value is colander.null:
                         # `colander.null` is never an appropriate
                         #  value to be placed on an SQLAlchemy object
                         #  so we translate it into `None`.
                         value = None
                setattr(context, attr, value)
            else:
                # Ignore attributes if they are not mapped
                logger.debug(
                    'SQLAlchemySchemaNode.objectify: %s not found on '
                    '%s. This property has been ignored.',
                    attr, self
                )
                continue

        return context

    def get_schema_from_column(self, prop, overrides):
        """Extended to handle JSON/JSONB.
        """

        # The name of the SchemaNode is the ColumnProperty key.
        name = prop.key
        kwargs = dict(name=name)
        column = prop.columns[0]
        typedecorator_overrides = getattr(column.type,
                                          self.ca_class_key, {}).copy()
        declarative_overrides = column.info.get(self.sqla_info_key, {}).copy()
        self.declarative_overrides[name] = declarative_overrides.copy()

        key = 'exclude'

        if key not in itertools.chain(declarative_overrides, overrides) \
           and typedecorator_overrides.pop(key, False):
            log.debug('Column %s skipped due to TypeDecorator overrides', name)
            return None

        if key not in overrides and declarative_overrides.pop(key, False):
            log.debug('Column %s skipped due to declarative overrides', name)
            return None

        if overrides.pop(key, False):
            log.debug('Column %s skipped due to imperative overrides', name)
            return None

        self.check_overrides(name, 'name', typedecorator_overrides,
                             declarative_overrides, overrides)

        for key in ['missing', 'default']:
            self.check_overrides(name, key, typedecorator_overrides, {}, {})

        # The SchemaNode built using the ColumnProperty has no children.
        children = []

        # The type of the SchemaNode will be evaluated using the Column type.
        # User can overridden the default type via Column.info or
        # imperatively using overrides arg in SQLAlchemySchemaNode.__init__

        # Support sqlalchemy.types.TypeDecorator
        column_type = getattr(column.type, 'impl', column.type)

        imperative_type = overrides.pop('typ', None)
        declarative_type = declarative_overrides.pop('typ', None)
        typedecorator_type = typedecorator_overrides.pop('typ', None)

        if imperative_type is not None:
            if hasattr(imperative_type, '__call__'):
                type_ = imperative_type()
            else:
                type_ = imperative_type
            log.debug('Column %s: type overridden imperatively: %s.',
                      name, type_)

        elif declarative_type is not None:
            if hasattr(declarative_type, '__call__'):
                type_ = declarative_type()
            else:
                type_ = declarative_type
            log.debug('Column %s: type overridden via declarative: %s.',
                      name, type_)

        elif typedecorator_type is not None:
            if hasattr(typedecorator_type, '__call__'):
                type_ = typedecorator_type()
            else:
                type_ = typedecorator_type
            log.debug('Column %s: type overridden via TypeDecorator: %s.',
                      name, type_)

        elif isinstance(column_type, Boolean):
            type_ = colander.Boolean()

        elif isinstance(column_type, Date):
            type_ = colander.Date()

        elif isinstance(column_type, DateTime):
            type_ = colander.DateTime(default_tzinfo=None)

        elif isinstance(column_type, Enum):
            type_ = colander.String()
            kwargs["validator"] = colander.OneOf(column.type.enums)

        elif isinstance(column_type, Float):
            type_ = colander.Float()

        elif isinstance(column_type, Integer):
            type_ = colander.Integer()

        elif isinstance(column_type, String):
            type_ = colander.String()
            kwargs["validator"] = colander.Length(0, column.type.length)

        elif isinstance(column_type, Numeric):
            type_ = colander.Decimal()

        elif isinstance(column_type, Time):
            type_ = colander.Time()

        elif isinstance(column_type, JSONB):
            type_ = colander.String()

        else:
            raise NotImplementedError(
                'Not able to derive a colander type from sqlalchemy '
                'type: %s  Please explicitly provide a colander '
                '`typ` for the "%s" Column.'
                % (repr(column_type), name)
            )

        """
        Add default values

        possible values for default in SQLA:
         1. plain non-callable Python value
              - give to Colander as a default
         2. SQL expression (derived from ColumnElement)
              - leave default blank and allow SQLA to fill
         3. Python callable with 0 or 1 args
            1 arg version takes ExecutionContext
              - leave default blank and allow SQLA to fill

        all values for server_default should be ignored for
        Colander default
        """
        if (isinstance(column.default, ColumnDefault)
                and column.default.is_scalar):
            kwargs["default"] = column.default.arg

        """
        Add missing values

        possible values for default in SQLA:
         1. plain non-callable Python value
              - give to Colander as a missing unless nullable
         2. SQL expression (derived from ColumnElement)
              - set missing to 'drop' to allow SQLA to fill this in
                and make it an unrequired field
         3. Python callable with 0 or 1 args
            1 arg version takes ExecutionContext
              - set missing to 'drop' to allow SQLA to fill this in
                and make it an unrequired field

        if nullable, then missing = colander.null (this has to be
        the case since some colander types won't accept `None` as
        a value, but all accept `colander.null`)

        all values for server_default should result in 'drop'
        for Colander missing

        autoincrement results in drop
        """
        if isinstance(column.default, ColumnDefault):
            if column.default.is_callable:
                kwargs["missing"] = drop
            elif column.default.is_clause_element:  # SQL expression
                kwargs["missing"] = drop
            elif column.default.is_scalar:
                kwargs["missing"] = column.default.arg
        elif column.nullable:
            kwargs["missing"] = colander.null
        elif isinstance(column.server_default, FetchedValue):
            kwargs["missing"] = drop  # value generated by SQLA backend
        elif (hasattr(column.table, "_autoincrement_column")
              and id(column.table._autoincrement_column) == id(column)):
            # this column is the autoincrement column, so we can drop
            # it if it's missing and let the database generate it
            kwargs["missing"] = drop

        kwargs.update(typedecorator_overrides)
        kwargs.update(declarative_overrides)
        kwargs.update(overrides)

        return colander.SchemaNode(type_, *children, **kwargs)

    def get_schema_from_relationship(self, prop, overrides):
        """Extended for property support."""

        # The name of the SchemaNode is the ColumnProperty key.
        name = prop.key
        kwargs = dict(name=name)
        declarative_overrides = prop.info.get(self.sqla_info_key, {}).copy()
        self.declarative_overrides[name] = declarative_overrides.copy()

        class_ = prop.mapper

        if declarative_overrides.pop('exclude', False):
            log.debug('Relationship %s skipped due to declarative overrides',
                      name)
            return None

        for key in ['name', 'typ']:
            self.check_overrides(name, key, {}, declarative_overrides,
                                 overrides)

        key = 'children'
        imperative_children = overrides.pop(key, None)
        declarative_children = declarative_overrides.pop(key, None)
        if imperative_children is not None:
            children = imperative_children
            msg = 'Relationship %s: %s overridden imperatively.'
            log.debug(msg, name, key)

        elif declarative_children is not None:
            children = declarative_children
            msg = 'Relationship %s: %s overridden via declarative.'
            log.debug(msg, name, key)

        else:
            children = None

        key = 'includes'
        imperative_includes = overrides.pop(key, None)
        declarative_includes = declarative_overrides.pop(key, None)
        if imperative_includes is not None:
            includes = imperative_includes
            msg = 'Relationship %s: %s overridden imperatively.'
            log.debug(msg, name, key)

        elif declarative_includes is not None:
            includes = declarative_includes
            msg = 'Relationship %s: %s overridden via declarative.'
            log.debug(msg, name, key)

        else:
            includes = None

        key = 'excludes'
        imperative_excludes = overrides.pop(key, None)
        declarative_excludes = declarative_overrides.pop(key, None)

        if imperative_excludes is not None:
            excludes = imperative_excludes
            msg = 'Relationship %s: %s overridden imperatively.'
            log.debug(msg, name, key)

        elif declarative_excludes is not None:
            excludes = declarative_excludes
            msg = 'Relationship %s: %s overridden via declarative.'
            log.debug(msg, name, key)

        else:
            excludes = None

        # see issue #25 in ColanderAlchemy for possible patch
        if includes is None and excludes is None:
            includes = [p.key for p in inspect(class_).column_attrs]

        key = 'overrides'
        imperative_rel_overrides = overrides.pop(key, None)
        declarative_rel_overrides = declarative_overrides.pop(key, None)

        if imperative_rel_overrides is not None:
            rel_overrides = imperative_rel_overrides
            msg = 'Relationship %s: %s overridden imperatively.'
            log.debug(msg, name, key)

        elif declarative_rel_overrides is not None:
            rel_overrides = declarative_rel_overrides
            msg = 'Relationship %s: %s overridden via declarative.'
            log.debug(msg, name, key)

        else:
            rel_overrides = None

        # Add default values for missing parameters.
        if prop.innerjoin:
            # Inner joined relationships imply it is mandatory
            missing = required
        else:
            # Any other join is thus optional
            missing = []
        kwargs['missing'] = missing

        kwargs.update(declarative_overrides)
        kwargs.update(overrides)

        if children is not None:
            if prop.uselist:
                # xToMany relationships.
                return SchemaNode(Sequence(), *children, **kwargs)
            else:
                # xToOne relationships.
                return SchemaNode(Mapping(), *children, **kwargs)

        node = PropertyAwareSQLAlchemySchemaNode(class_,
                                    name=name,
                                    includes=includes,
                                    excludes=excludes,
                                    overrides=rel_overrides,
                                    missing=missing)

        if prop.uselist:
            node = SchemaNode(Sequence(), node, **kwargs)

        node.name = name

        return node

    def deserialize(self, cstruct):

        # Pass-through of real SQL alchemy objects, as generated by widget deserialize()
        marker = object()
        orig = cstruct.get("_orig", marker)
        if orig is not marker:
            return orig

        result = super(PropertyAwareSQLAlchemySchemaNode, self).deserialize(cstruct)
        return result