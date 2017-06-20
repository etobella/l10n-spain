# -*- coding: utf-8 -*-
# © 2017 Creu Blanca
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from datetime import datetime
import os
import subprocess
import logging
import base64

from lxml import etree

from odoo import models, fields, tools, _
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DATETIME_FORMAT
from odoo.exceptions import Warning as UserError, ValidationError


class AccountInvoice(models.Model):
    _inherit = "account.invoice"

    correction_method = fields.Selection(
        selection=[
            ('01', 'Rectificación íntegra'),
            ('02', 'Rectificación por diferencias'),
            ('03', 'Rectificación por descuento por volumen de operaciones '
                   'durante un periodo'),
            ('04', 'Autorizadas por la Agencia Tributaria')
        ]
    )

    refund_reason = fields.Selection(
        selection=[
            ('01', 'Número de la factura'),
            ('02', 'Serie de la factura'),
            ('03', 'Fecha expedición'),
            ('04', 'Nombre y apellidos/Razón social - Emisor'),
            ('05', 'Nombre y apellidos/Razón social - Receptor'),
            ('06', 'Identificación fiscal Emisor/Obligado'),
            ('07', 'Identificación fiscal Receptor'),
            ('08', 'Domicilio Emisor/Obligado'),
            ('09', 'Domicilio Receptor'),
            ('10', 'Detalle Operación'),
            ('11', 'Porcentaje impositivo a aplicar'),
            ('12', 'Cuota tributaria a aplicar'),
            ('13', 'Fecha/Periodo a aplicar'),
            ('14', 'Clase de factura'),
            ('15', 'Literales legales'),
            ('16', 'Base imponible'),
            ('80', 'Cálculo de cuotas repercutidas'),
            ('81', 'Cálculo de cuotas retenidas'),
            ('82', 'Base imponible modificada por devolución de envases'
                   '/embalajes'),
            ('83', 'Base imponible modificada por descuentos y '
                   'bonificaciones'),
            ('84', 'Base imponible modificada por resolución firme, judicial '
                   'o administrativa'),
            ('85', 'Base imponible modificada cuotas repercutidas no '
                   'satisfechas. Auto de declaración de concurso')
        ]
    )

    def get_exchange_rate(self, euro_rate, currency_rate):
        if not euro_rate and not currency_rate:
            return datetime.today().strftime('%Y-%m-%d')
        if not currency_rate:
            return datetime.strptime(euro_rate.name,
                                     DATETIME_FORMAT).strftime('%Y-%m-%d')
        if not euro_rate:
            return datetime.strptime(currency_rate.name,
                                     DATETIME_FORMAT).strftime('%Y-%m-%d')
        currency_date = datetime.strptime(currency_rate.name, DATETIME_FORMAT)
        euro_date = datetime.strptime(currency_rate.name, DATETIME_FORMAT)
        if currency_date < euro_date:
            return currency_date.strftime('%Y-%m-%d')
        return euro_date.strftime('%Y-%m-%d')

    def get_refund_reason_string(self):
        return dict(
            self.fields_get(allfields=['refund_reason'])['refund_reason'][
                'selection'])[self.refund_reason]

    def get_correction_method_string(self):
        return dict(
            self.fields_get(allfields=['correction_method'])[
                'correction_method']['selection'])[self.correction_method]

    def validate_facturae_fields(self):
        if len(self.partner_id.vat) < 3:
            raise ValidationError(_('Partner vat is too small'))
        if len(self.company_id.vat) < 3:
            raise ValidationError(_('Company vat is too small'))
        if self.payment_mode_id.facturae_code == '02':
            if len(self.mandate_id.partner_bank_id.bank_id.bic) != 11:
                raise ValidationError(_('Mandate account BIC must be 11'))
            if len(self.mandate_id.partner_bank_id.acc_number) < 5:
                raise ValidationError(_('Mandate account is too small'))
        else:
            if self.partner_bank_id.bank_id.bic and len(
                    self.partner_bank_id.bank_id.bic) != 11:
                raise ValidationError(_('Selected account BIC must be 11'))
            if len(self.partner_bank_id.acc_number) < 5:
                raise ValidationError(_('Selected account is too small'))
        return

    def get_facturae(self, firmar_facturae):

        def _call_java_signer(cert, password, request):
            path = os.path.realpath(os.path.dirname(__file__))
            path += '/../java/'
            command = ['java', '-jar', '-Djava.net.useSystemProxies=true']
            command += [path + 'FacturaeSigner.jar']
            command += [base64.b64encode(request)]
            command += [cert]
            command += ['pkcs12']
            command += [password]
            p = subprocess.Popen(command, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            out, err = p.communicate()
            if len(err) > 0:
                logging.warn("Warning - result was " + err.decode('utf-8'))
            return out

        logger = logging.getLogger("facturae")

        self.validate_facturae_fields()

        report = self.env.ref('l10n_es_facturae.report_facturae')
        xml_facturae = self.env['report'].get_html([self.id],
                                                   report.report_name)
        tree = etree.fromstring(
            xml_facturae, etree.XMLParser(remove_blank_text=True))
        xml_facturae = etree.tostring(tree,
                                      pretty_print=True,
                                      xml_declaration=True,
                                      encoding='UTF-8')
        self._validate_facturae(xml_facturae, logger)
        if self.company_id.facturae_cert and firmar_facturae:
            file_name = (_(
                'facturae') + '_' + self.number + '.xsig').replace('/', '-')
            invoice_file = _call_java_signer(
                self.company_id.facturae_cert,
                self.company_id.facturae_cert_password,
                xml_facturae)
        else:
            invoice_file = xml_facturae
            file_name = (_(
                'facturae') + '_' + self.number + '.xml').replace('/', '-')

        return invoice_file, file_name

    @staticmethod
    def _validate_facturae(xml_string, logger):
        facturae_schema = etree.XMLSchema(
            etree.parse(tools.file_open(
                "Facturaev3_2.xsd", subdir="addons/l10n_es_facturae/data")))
        try:
            facturae_schema.assertValid(etree.fromstring(xml_string))
        except Exception, e:
            logger.warning(
                "The XML file is invalid against the XML Schema Definition")
            logger.warning(xml_string)
            logger.warning(e)
            raise UserError(
                _("The generated XML file is not valid against the official "
                  "XML Schema Definition. The generated XML file and the "
                  "full error have been written in the server logs. Here "
                  "is the error, which may give you an idea on the cause "
                  "of the problem : %s") % unicode(e))
        return True
